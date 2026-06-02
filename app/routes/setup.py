from flask import Blueprint, render_template, request, redirect, url_for, current_app, flash
from werkzeug.security import generate_password_hash
import re

setup_bp = Blueprint("setup", __name__)


def slugify(text):
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text


@setup_bp.route("/setup", methods=["GET", "POST"])
def setup():
    db = current_app.db
    # If users already exist, setup is complete — go to login
    if db.get_users():
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        username     = request.form.get("username", "").strip()
        password     = request.form.get("password", "")
        confirm      = request.form.get("confirm_password", "")
        display_name = request.form.get("display_name", "").strip() or username
        email        = request.form.get("email", "").strip() or None

        errors = []
        if not username:
            errors.append("Username is required.")
        if not re.match(r'^[a-z0-9_-]+$', slugify(username)):
            errors.append("Username may only contain letters, numbers, hyphens, and underscores.")
        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        if password != confirm:
            errors.append("Passwords do not match.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("setup.html")

        slug = slugify(username)
        db.create_user(slug, username, email, generate_password_hash(password), display_name, is_admin=1)
        flash("Admin account created — you can now log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("setup.html")