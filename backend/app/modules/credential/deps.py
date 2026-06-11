"""U12 凭据模块依赖注入。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.modules.auth.deps import SessionDep
from app.modules.credential.service import CredentialService


def get_credential_service(session: SessionDep) -> CredentialService:
    return CredentialService(session)


CredentialServiceDep = Annotated[CredentialService, Depends(get_credential_service)]


__all__ = ["CredentialServiceDep", "get_credential_service"]
