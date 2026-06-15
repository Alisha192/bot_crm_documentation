from flask_login import UserMixin

ADMIN_USERS = {
    "admin": "admin123"
}

class Admin(UserMixin):
    def __init__(self, username):
        self.id = username
