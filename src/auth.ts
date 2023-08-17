import {useContextStore} from '@/stores/context'

const goToLogin = (to: any, next: any) => {
  next({
    path: '/',
    query: {
      error: to.query.error,
      redirect: to.fullPath
    }
  })
}

export default {
  requiresAdmin: (to: any, from: any, next: any) => {
    const currentUser = useContextStore().currentUser
    if (currentUser.isAdmin) {
      next()
    } else if (currentUser.isAuthenticated) {
      const message = 'Unauthorized'
      useContextStore().setApplicationState(401)
      next(`/error?error=${message}`)
    } else {
      goToLogin(to, next)
    }
  },
  requiresAuthenticated: (to: any, from: any, next: any) => {
    if (useContextStore().currentUser.isAuthenticated) {
      next()
    } else {
      goToLogin(to, next)
    }
  }
}
