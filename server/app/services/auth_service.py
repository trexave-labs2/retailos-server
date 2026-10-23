from flask import session,request
from sqlalchemy.exc import IntegrityError
from server.app.models.user import User
from server.app.models.store import Store
from server.app.utils.response import Response
from server.app.utils.security import hash_password,verify_password
from server.app.utils.validators import validate_email,validate_required_string,validate_password
from server.app.extensions import db

class AuthService:
    def __init__(self): pass

    def _user_data(self,user):
        store=Store.query.filter_by(user_id=user.id).first()
        return {"id":user.id,"username":user.name,"email":user.email,"store_id":store.id if store else None,"store_name":store.name if store else None,"created_at":user.created_at.isoformat()}

    def register_user(self):
        try:
            data=request.get_json(silent=True)
            if not isinstance(data,dict):return Response.error_response("VALIDATION_ERROR","Invalid input data",{"payload":"Payload must be a JSON object"}),400
            username,email,password=data.get("username"),data.get("email"),data.get("password")
            fields={}
            e=validate_required_string(username,"Username",2)
            if e:fields["username"]=e
            e=validate_email(email)
            if e:fields["email"]=e
            e=validate_password(password)
            if e:fields["password"]=e
            if fields:return Response.error_response("VALIDATION_ERROR","Invalid input data",fields),400
            email=email.strip().lower()
            if User.query.filter_by(email=email).first():return Response.error_response("EMAIL_ALREADY_EXISTS","Email already exists",{"email":"Email is already registered"}),400
            user=User(name=username.strip(),email=email,password_hash=hash_password(password))
            db.session.add(user);db.session.flush()
            store=Store(user_id=user.id,name=f"{user.name}'s Store")
            db.session.add(store);db.session.commit()
            return Response.success_response({"user":self._user_data(user)},"REGISTRATION_SUCCESSFUL"),201
        except IntegrityError:
            db.session.rollback();return Response.error_response("CONFLICT_ERROR","Database constraint violation",{}),409

    def login_user(self):
        data=request.get_json(silent=True)
        if not isinstance(data,dict):return Response.error_response("VALIDATION_ERROR","Invalid JSON object",{}),400
        email,password=data.get("email"),data.get("password");fields={}
        e=validate_email(email)
        if e:fields["email"]=e
        e=validate_password(password)
        if e:fields["password"]=e
        if fields:return Response.error_response("VALIDATION_ERROR","Invalid input data",fields),400
        user=User.query.filter_by(email=email.strip().lower()).first()
        if not user or not verify_password(password,user.password_hash):return Response.error_response("AUTH_INVALID_CREDENTIALS","Invalid email or password",{}),401
        session.clear();session["user_id"]=user.id;session["session_version"]=user.session_version
        return Response.success_response({"user":self._user_data(user)},"LOGIN_SUCCESSFUL"),200

    def current_user(self):
        user=User.query.filter_by(id=session.get("user_id")).first()
        if not user:return Response.error_response("USER_NOT_FOUND","User not found",{}),401
        return Response.success_response({"user":self._user_data(user)},"CURRENT_USER_RETRIEVED"),200

    def change_password(self):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", {"payload": "Payload must be a JSON object"}), 400
        current_password = data.get("current_password")
        new_password = data.get("new_password")
        fields = {}
        current_password_error = validate_password(current_password, minimum_length=1)
        if current_password_error:
            fields["current_password"] = "Current password is required or exceeds the supported password length"
        error = validate_password(new_password)
        if error:
            fields["new_password"] = error
        if fields:
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", fields), 400

        user = User.query.filter_by(id=session.get("user_id")).first()
        if not user:
            session.clear()
            return Response.error_response("USER_NOT_FOUND", "User not found", {}), 401
        if not verify_password(current_password, user.password_hash):
            return Response.error_response("AUTH_INVALID_CURRENT_PASSWORD", "Current password is incorrect", {"current_password": "Current password is incorrect"}), 401
        if verify_password(new_password, user.password_hash):
            return Response.error_response("PASSWORD_UNCHANGED", "New password must differ from the current password", {"new_password": "Choose a different password"}), 400

        user.password_hash = hash_password(new_password)
        user.session_version += 1
        db.session.commit()
        session.clear()
        return Response.success_response({}, "PASSWORD_CHANGED_LOGIN_AGAIN"), 200

    def logout_user(self):
        user=User.query.filter_by(id=session.get("user_id")).first();session.clear()
        if not user:return Response.error_response("USER_NOT_FOUND","User not found",{}),401
        return Response.success_response({"user":{"id":user.id,"username":user.name,"email":user.email}},"LOGOUT_SUCCESSFUL"),200
