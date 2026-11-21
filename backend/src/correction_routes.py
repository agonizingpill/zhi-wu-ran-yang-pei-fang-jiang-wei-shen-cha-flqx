from fastapi import APIRouter,Depends
from .database import session
from .auth import current,admin
from .correction_schemas import CorrectionInput
from . import correction_service
router=APIRouter()
@router.get('/issue-corrections')
def overview(user=Depends(current),db=Depends(session)):
    return correction_service.overview(db)
@router.post('/issue-corrections')
def confirm(data:CorrectionInput,user=Depends(admin),db=Depends(session)):
    return correction_service.confirm(db,data,user)
