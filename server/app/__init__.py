import os

from flask import Flask,request,make_response
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy.exc import IntegrityError
from flask_migrate import Migrate

from server.app.config import Config, ProductionConfig
from server.app.extensions import db, limiter
from server.app.routes.alert_routes import alert_bp
from server.app.routes.audit_routes import audit_bp
from server.app.routes.auth_routes import auth_bp
from server.app.routes.settings_routes import settings_bp
from server.app.routes.customer_routes import customer_bp
from server.app.routes.category_routes import category_bp
from server.app.routes.dashboard_routes import dashboard_bp
from server.app.routes.health_routes import health_bp
from server.app.routes.feedback_routes import feedback_bp
from server.app.routes.product_routes import product_bp
from server.app.routes.sales_routes import sales_bp
from server.app.routes.refund_routes import refund_bp
from server.app.routes.stock_receiving_routes import stock_receiving_bp
from server.app.routes.subscription_routes import subscription_bp
from server.app.utils.response import Response


def create_app(config_class=Config):
    app=Flask(__name__)
    app.config.from_object(config_class)

    if config_class is ProductionConfig:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0, x_port=0, x_prefix=0)

    if config_class is ProductionConfig:
        app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY")
        from server.app.config import normalize_database_url
        app.config["SQLALCHEMY_DATABASE_URI"] = normalize_database_url(os.environ.get("DATABASE_URL"))

    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("SECRET_KEY must be configured")
    if config_class is ProductionConfig and not app.config.get("SQLALCHEMY_DATABASE_URI"):
        raise RuntimeError("DATABASE_URL must be configured in production")

    if config_class is ProductionConfig:
        rate_limit_storage_uri = os.environ.get("RATELIMIT_STORAGE_URI")
        if not rate_limit_storage_uri or not rate_limit_storage_uri.startswith(("redis://", "rediss://")):
            raise RuntimeError("RATELIMIT_STORAGE_URI must be configured in production with a redis:// or rediss:// shared Redis connection URL")
    else:
        rate_limit_storage_uri = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")

    app.config["RATELIMIT_STORAGE_URI"] = rate_limit_storage_uri
    limiter.init_app(app)
    db.init_app(app)
    app.register_blueprint(auth_bp,url_prefix="/auth")
    app.register_blueprint(settings_bp,url_prefix="/settings")
    app.register_blueprint(product_bp,url_prefix="/product")
    app.register_blueprint(category_bp, url_prefix="/categories")
    app.register_blueprint(stock_receiving_bp,url_prefix="/product/receivings")
    app.register_blueprint(sales_bp,url_prefix="/sales")
    app.register_blueprint(refund_bp,url_prefix="/sales/refunds")
    app.register_blueprint(customer_bp,url_prefix="/customers")
    app.register_blueprint(alert_bp,url_prefix="/alerts")
    app.register_blueprint(audit_bp,url_prefix="/audit")
    app.register_blueprint(dashboard_bp,url_prefix="/dashboard")
    app.register_blueprint(health_bp,url_prefix="/health")
    app.register_blueprint(feedback_bp,url_prefix="/feedback")
    app.register_blueprint(subscription_bp,url_prefix="/subscriptions")
    Migrate(app,db)

    allowed_origins = {origin.strip().rstrip("/") for origin in app.config["CORS_ORIGINS"].split(",") if origin.strip()}
    proxy_diagnostic_logged = False

    @app.before_request
    def log_proxy_address_once():
        nonlocal proxy_diagnostic_logged
        if not proxy_diagnostic_logged:
            app.logger.info("Proxy diagnostic: request.remote_addr=%s", request.remote_addr)
            proxy_diagnostic_logged = True

    @app.before_request
    def enforce_client_origin():
        origin=request.headers.get("Origin")
        if request.method=="OPTIONS":
            if origin and origin.rstrip("/") not in allowed_origins:
                return Response.error_response("ORIGIN_NOT_ALLOWED","This client origin is not allowed",{}),403
            return make_response("",204)
        if origin and origin.rstrip("/") not in allowed_origins:
            return Response.error_response("ORIGIN_NOT_ALLOWED","This client origin is not allowed",{}),403
        return None

    @app.after_request
    def add_client_cors(response):
        origin=request.headers.get("Origin")
        if origin and origin.rstrip("/") in allowed_origins:
            response.headers["Access-Control-Allow-Origin"]=origin
            response.headers["Access-Control-Allow-Credentials"]="true"
            response.headers["Access-Control-Allow-Headers"]="Content-Type"
            response.headers["Access-Control-Allow-Methods"]="GET,POST,PATCH,PUT,DELETE,OPTIONS"
            response.headers["Vary"]="Origin"
        return response

    @app.errorhandler(400)
    def bad_request(error):
        return Response.error_response("BAD_REQUEST","The request could not be understood",{}),400

    @app.errorhandler(429)
    def rate_limited(error):
        return Response.error_response("RATE_LIMITED","Too many attempts. Try again in a minute.",{}),429

    @app.errorhandler(404)
    def not_found(error):
        return Response.error_response("NOT_FOUND","The requested resource was not found",{}),404

    @app.errorhandler(405)
    def method_not_allowed(error):
        return Response.error_response("METHOD_NOT_ALLOWED","The requested method is not allowed",{}),405

    @app.errorhandler(IntegrityError)
    def database_conflict(error):
        db.session.rollback()
        return Response.error_response("CONFLICT_ERROR","The request conflicts with existing data",{}),409

    @app.errorhandler(Exception)
    def internal_server_error(error):
        db.session.rollback()
        app.logger.exception("Unhandled server exception")
        return Response.error_response("INTERNAL_SERVER_ERROR","An unexpected server error occurred",{}),500

    return app
