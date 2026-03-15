import os
from functools import wraps

from flask import Flask, flash, redirect, render_template, request, session, url_for

from stock_check.app.services.admin_service import AdminService, NotifierSettings


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.getenv("STOCK_CHECK_WEB_SECRET", "stock-check-secret")
    service = AdminService()

    def login_required(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not session.get("authorized"):
                return redirect(url_for("login"))
            return view_func(*args, **kwargs)

        return wrapped

    @app.get("/")
    def root():
        if session.get("authorized"):
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            access_key = request.form.get("access_key", "")
            if service.verify_access_key(access_key):
                session["authorized"] = True
                flash("인증에 성공했습니다.", "success")
                return redirect(url_for("dashboard"))
            flash("접속 키가 올바르지 않습니다.", "error")
        return render_template("login.html")

    @app.post("/logout")
    def logout():
        session.clear()
        flash("로그아웃되었습니다.", "success")
        return redirect(url_for("login"))

    @app.get("/dashboard")
    @login_required
    def dashboard():
        sites = ["cultizm", "hyundai"]
        counts = {site: len(service.load_targets(site)) for site in sites}
        return render_template("dashboard.html", target_counts=counts)

    @app.get("/targets/<site>")
    @login_required
    def targets(site: str):
        rows = service.load_targets(site)
        return render_template("targets.html", site=site, rows=rows)

    @app.post("/targets/<site>/add")
    @login_required
    def add_target(site: str):
        keyword = request.form.get("keyword", "").strip()
        sizes = request.form.get("sizes", "").strip()
        if not keyword:
            flash("상품명을 입력해주세요.", "error")
            return redirect(url_for("targets", site=site))
        service.add_target(site, keyword, sizes)
        flash("타겟이 추가되었습니다.", "success")
        return redirect(url_for("targets", site=site))

    @app.post("/targets/<site>/<int:idx>/update")
    @login_required
    def update_target(site: str, idx: int):
        keyword = request.form.get("keyword", "").strip()
        sizes = request.form.get("sizes", "").strip()
        try:
            service.update_target(site, idx, keyword, sizes)
            flash("타겟이 수정되었습니다.", "success")
        except IndexError:
            flash("수정 대상 인덱스가 올바르지 않습니다.", "error")
        return redirect(url_for("targets", site=site))

    @app.post("/targets/<site>/<int:idx>/delete")
    @login_required
    def delete_target(site: str, idx: int):
        try:
            service.delete_target(site, idx)
            flash("타겟이 삭제되었습니다.", "success")
        except IndexError:
            flash("삭제 대상 인덱스가 올바르지 않습니다.", "error")
        return redirect(url_for("targets", site=site))

    @app.route("/settings/notifier", methods=["GET", "POST"])
    @login_required
    def notifier_settings():
        if request.method == "POST":
            settings = NotifierSettings(
                smtp_server=request.form.get("smtp_server", ""),
                smtp_port=request.form.get("smtp_port", ""),
                smtp_user=request.form.get("smtp_user", ""),
                smtp_password=request.form.get("smtp_password", ""),
                email_recipients=request.form.get("email_recipients", ""),
                telegram_bot_token=request.form.get("telegram_bot_token", ""),
                telegram_chat_id=request.form.get("telegram_chat_id", ""),
            )
            service.save_notifier_settings(settings)
            flash("알림 설정이 저장되었습니다.", "success")
            return redirect(url_for("notifier_settings"))

        current = service.load_notifier_settings()
        return render_template("settings.html", settings=current)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.getenv("STOCK_CHECK_WEB_PORT", "8080")))
