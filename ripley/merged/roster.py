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

from flask import current_app as app
from ripley.externals import canvas
from ripley.externals.s3 import get_signed_urls
from ripley.lib.canvas_utils import section_id_from_canvas_sis_section_id


def canvas_site_roster(canvas_site_id):
    canvas_sections = canvas.get_course_sections(canvas_site_id)
    sections = [_section(cs) for cs in canvas_sections if cs.sis_section_id]
    sections_by_id = {s['id']: s for s in sections}
    canvas_students = canvas.get_course_students(canvas_site_id, per_page=100)
    students = [_student(s, sections_by_id) for s in canvas_students]
    _merge_photo_urls(students)
    return {
        'sections': sections,
        'students': students,
    }


def _merge_photo_urls(students):
    def _photo_key(student):
        return f"{app.config['DATA_LOCH_S3_PHOTO_PATH']}/{student['loginId']}.jpg"

    photo_urls = get_signed_urls(
        bucket=app.config['DATA_LOCH_S3_PHOTO_BUCKET'],
        keys=[_photo_key(student) for student in students if student['loginId']],
        expiration=app.config['PHOTO_SIGNED_URL_EXPIRES_IN_SECONDS'],
    )
    for student in students:
        student['photoUrl'] = photo_urls.get(_photo_key(student), None)


def _section(canvas_section):
    return {
        'id': section_id_from_canvas_sis_section_id(canvas_section.sis_section_id),
        'name': canvas_section.name,
        'sisId': canvas_section.sis_section_id,
    }


def _student(canvas_student, sections_by_id):
    def _get(attr):
        value = None
        if hasattr(canvas_student, attr):
            value = getattr(canvas_student, attr)
        return value
    names = canvas_student.sortable_name.split(', ')
    enrollments = canvas_student.enrollments if hasattr(canvas_student, 'enrollments') else []
    section_ids = [section_id_from_canvas_sis_section_id(e['sis_section_id']) for e in enrollments if e['sis_section_id']]
    return {
        'email': _get('email'),
        'enrollStatus': enrollments[0]['enrollment_state'] if enrollments else None,
        'firstName': names[1],
        'id': canvas_student.id,
        'lastName': names[0],
        'loginId': _get('login_id'),
        'sections': [sections_by_id[section_id] for section_id in list(set(section_ids))],
        'studentId': canvas_student.sis_user_id,
    }