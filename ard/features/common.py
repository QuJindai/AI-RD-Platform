"""Shared identity and service dependencies for feature routers."""
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from ard.security import Identity
from ard.service import Service


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def get_service(request: Request):
    return request.app.state.service


def get_user(request: Request):
    return request.app.state.security.resolve(request)


Svc = Annotated[Service, Depends(get_service)]
User = Annotated[Identity, Depends(get_user)]


def admin(user):
    if user.role != "admin":
        raise HTTPException(403, "此操作需要管理员权限")


def install_once(app, router, key):
    installed = getattr(app.state, "features", set())
    if key not in installed:
        app.include_router(router)
        app.state.features = installed | {key}
