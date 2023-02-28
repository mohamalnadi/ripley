"""
Copyright ©2023. The Regents of the University of California (Regents). All Rights Reserved.

Permission to use, copy, modify, and distribute this software and its documentation
for educational, research, and not-for-profit purposes, without fee and without a
signed licensing agreement, is hereby granted, provided that the above copyright
notice, this paragraph and the following two paragraphs appear in all copies,
modifications, and distributions.

Contact The Office of Technology Licensing, UC Berkeley, 2150 Shattuck Avenue,
Suite 510, Berkeley, CA 94720-1620, (510) 643-7201, otl@berkeley.edu,
http://ipira.berkeley.edu/industry-info for commercial licensing opportunities.

IN NO EVENT SHALL REGENTS BE LIABLE TO ANY PARTY FOR DIRECT, INDIRECT, SPECIAL,
INCIDENTAL, OR CONSEQUENTIAL DAMAGES, INCLUDING LOST PROFITS, ARISING OUT OF
THE USE OF THIS SOFTWARE AND ITS DOCUMENTATION, EVEN IF REGENTS HAS BEEN ADVISED
OF THE POSSIBILITY OF SUCH DAMAGE.

REGENTS SPECIFICALLY DISCLAIMS ANY WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE. THE
SOFTWARE AND ACCOMPANYING DOCUMENTATION, IF ANY, PROVIDED HEREUNDER IS PROVIDED
"AS IS". REGENTS HAS NO OBLIGATION TO PROVIDE MAINTENANCE, SUPPORT, UPDATES,
ENHANCEMENTS, OR MODIFICATIONS.
"""

from urllib.parse import urlencode

import cas
from flask import current_app as app, flash, redirect, request, url_for
from flask_login import current_user, login_required, logout_user
from pylti1p3.contrib.flask import (FlaskCacheDataStorage, FlaskMessageLaunch, FlaskOIDCLogin, FlaskRequest)
from pylti1p3.exception import LtiException
from pylti1p3.tool_config import ToolConfJsonFile
from ripley import cache
from ripley.api.errors import BadRequestError, InternalServerError, ResourceNotFoundError
from ripley.api.util import start_login_session
from ripley.lib.http import add_param_to_url, tolerant_jsonify
from ripley.models.user import User


class MessageLaunch(FlaskMessageLaunch):

    def validate_nonce(self):
        # Temporary(?) workaround for "Invalid nonce" error from pylti1p3.
        try:
            super().validate_nonce()
        except LtiException as e:
            nonce = self._get_jwt_body().get('nonce')
            app.logger.warn(f'Nonce validation failed; skipping. {e} nonce={nonce}')
        return self


@app.route('/api/auth/cas_login_url')
def cas_login_url():
    target_url = request.referrer or None
    return tolerant_jsonify({
        'casLoginUrl': _cas_client(target_url).get_login_url(),
    })


@app.route('/api/auth/dev_auth', methods=['POST'])
def dev_auth_login():
    if app.config['DEV_AUTH_ENABLED']:
        params = request.get_json() or {}
        uid = params.get('uid')
        app.logger.debug(f'Dev-auth login attempt by UID {uid}')
        password = params.get('password')
        if password != app.config['DEV_AUTH_PASSWORD']:
            app.logger.debug(f'UID {uid} failed dev-auth login: bad password.')
            return tolerant_jsonify({'message': 'Invalid credentials'}, 401)
        user = User(uid)
        if not user.is_active:
            msg = f'Sorry, {uid} is not authorized to use this tool.'
            return tolerant_jsonify({'message': msg}, 403)
        api_json = start_login_session(user)
        app.logger.debug(f'Successful dev-auth login for {api_json}.')
        return api_json
    else:
        app.logger.debug('Dev-auth attempt when DEV_AUTH_ENABLED == False.')
        raise ResourceNotFoundError('Unknown path')


@app.route('/api/auth/jwks')
def get_jwk_set():
    lti_config_path = app.config['LTI_CONFIG_PATH']
    try:
        tool_conf = ToolConfJsonFile(lti_config_path)
        key_set = tool_conf.get_jwks()
        return tolerant_jsonify(key_set)
    except Exception as e:
        app.logger.error(f'Failed to generate LTI keys: {e.__class__.__name__}: {e}')
        raise InternalServerError({'message': str(e)})


@app.route('/api/auth/lti_launch', methods=['GET', 'POST'])
def lti_launch():
    lti_config_path = app.config['LTI_CONFIG_PATH']
    flask_request = FlaskRequest()
    try:
        tool_conf = ToolConfJsonFile(lti_config_path)
        launch_data_storage = FlaskCacheDataStorage(cache)

        message_launch = MessageLaunch(flask_request, tool_conf, launch_data_storage=launch_data_storage)
        message_launch_data = message_launch.get_launch_data()
        custom_fields = message_launch_data.get('https://purl.imsglobal.org/spec/lti/claim/custom', {})
        uid = custom_fields.get('canvas_user_login_id')
        canvas_api_domain = custom_fields.get('canvas_api_domain')

        user = User(uid, canvas_api_domain)
        app.logger.info(f"""Logged in during LTI launch as user {user}""")
        params = f'canvasApiDomain={canvas_api_domain}'
        return start_login_session(user, redirect_path=f'/welcome?{params}')
    except Exception as e:
        app.logger.error(f'Failure to launch: {e.__class__.__name__}: {e}')
        raise InternalServerError({'message': str(e)})


@app.route('/api/auth/lti_login', methods=['GET', 'POST'])
def lti_login():
    lti_config_path = app.config['LTI_CONFIG_PATH']
    flask_request = FlaskRequest()
    target_link_uri = flask_request.get_param('target_link_uri')
    if not target_link_uri:
        raise BadRequestError('Required parameters are missing.')
    try:
        tool_conf = ToolConfJsonFile(lti_config_path)
        launch_data_storage = FlaskCacheDataStorage(cache)
        oidc_login = FlaskOIDCLogin(flask_request, tool_conf, launch_data_storage=launch_data_storage)

        response = oidc_login.enable_check_cookies().redirect(target_link_uri)
        app.logger.info(f'Redirecting to target_link_uri {target_link_uri}')
        return response
    except Exception as e:
        app.logger.error(f'OIDC login failed: {e.__class__.__name__}: {e}')
        raise InternalServerError({'message': str(e)})


@app.route('/api/auth/logout')
@login_required
def logout():
    logout_user()
    redirect_url = app.config['VUE_LOCALHOST_BASE_URL'] or request.url_root
    cas_logout_url = _cas_client().get_logout_url(redirect_url=redirect_url)
    response = tolerant_jsonify({
        'casLogoutUrl': cas_logout_url,
        **current_user.to_api_json(),
    })
    response.delete_cookie(
        key=f'{current_user.canvas_api_domain}',
        samesite='None',
        secure=True,
    )
    return response


@app.route('/cas/callback', methods=['GET', 'POST'])
def cas_login():
    ticket = request.args['ticket']
    target_url = request.args.get('url')
    uid, attributes, proxy_granting_ticket = _cas_client(target_url).verify_ticket(ticket)
    app.logger.info(f'Logged into CAS as user {uid}')
    user = User(uid)
    if user.is_active:
        flash('Logged in successfully.')
        redirect_path = target_url or '/'
        return start_login_session(user, redirect_path=redirect_path)
    else:
        redirect_path = add_param_to_url('/', ('error', f'Sorry, {user.name} is not authorized to use this tool.'))
        return redirect(redirect_path)


def _cas_client(target_url=None):
    cas_server = app.config['CAS_SERVER']
    # One (possible) advantage this has over "request.base_url" is that it embeds the configured SERVER_NAME.
    service_url = url_for('.cas_login', _external=True)
    if target_url:
        service_url = service_url + '?' + urlencode({'url': target_url})
    return cas.CASClientV3(server_url=cas_server, service_url=service_url)