from flask import Flask, redirect, url_for
from flask_login import LoginManager
from auth import Admin
from routes.dashboard import dashboard_bp
from routes.orders import orders_bp
from routes.menu import menu_bp
from routes.auth import auth_bp
from admin_panel.broadcast import broadcast_bp

app = Flask(__name__)
app.secret_key = "super-secret-key"

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return Admin(user_id)

# Роуты
app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(broadcast_bp)
app.register_blueprint(orders_bp, url_prefix="/orders")
app.register_blueprint(menu_bp, url_prefix="/menu")

@app.route("/")
def root():
    return redirect("/orders")

app.run(
    debug=True,
    use_reloader=False
)
