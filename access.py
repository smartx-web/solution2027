"""Request-scoped permissions. Unset context is reserved for local setup/tests."""
from contextvars import ContextVar

current_user=ContextVar('current_user',default=None)

def is_admin():
    user=current_user.get()
    return user is None or user['role']=='admin'

def can_write():
    user=current_user.get()
    return user is None or user['role'] in ('admin','coordinator')

def require_admin():
    if not is_admin(): raise PermissionError('Administrator access is required.')

def require_write():
    if not can_write(): raise PermissionError('This account has viewing access only.')

def in_scope(pu):
    user=current_user.get()
    if user and user['role']=='agent': return str(pu['id'])==str(user.get('pu_id'))
    if user is None or user['role']!='coordinator': return True
    return pu['lga']==user['lga'] and (not user['wards'] or pu['ward'] in user['wards'])

def require_scope(pu):
    if pu is None or not in_scope(pu): raise PermissionError('This polling unit is outside your assigned area.')


def require_results_write():
    user=current_user.get()
    if user and user['role'] not in ('admin','coordinator','agent'):
        raise PermissionError('This account has viewing access only.')
