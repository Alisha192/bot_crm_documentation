from flask import Blueprint, render_template, request, redirect, url_for
from flask_login import login_user, logout_user
from auth import Admin, ADMIN_USERS

auth_bp = Blueprint("auth", __name__)

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if username in ADMIN_USERS and ADMIN_USERS[username] == password:
            login_user(Admin(username))
            return redirect(url_for("dashboard.dashboard"))

        return render_template("login.html", error="Неверный логин или пароль")

    return render_template("login.html")


@auth_bp.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
