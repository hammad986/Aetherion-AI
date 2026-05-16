from flask import Blueprint, render_template

ui_bp = Blueprint('ui_routes', __name__)

@ui_bp.route("/")
def index():
    return render_template("index.html")

@ui_bp.route("/docs")
def docs():
    return render_template("docs.html")

@ui_bp.route("/privacy-policy")
def privacy_policy():
    return render_template("privacy-policy.html")

@ui_bp.route("/terms-of-service")
def terms_of_service():
    return render_template("terms-of-service.html")

@ui_bp.route("/refund-policy")
def refund_policy():
    return render_template("refund-policy.html")
