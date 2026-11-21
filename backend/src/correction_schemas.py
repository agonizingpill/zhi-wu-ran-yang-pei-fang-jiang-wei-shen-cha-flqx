from pydantic import BaseModel,Field,field_validator,ConfigDict
class CorrectionInput(BaseModel):
    model_config=ConfigDict(allow_inf_nan=False)
    batch_low_id:int=Field(gt=0)
    batch_high_id:int=Field(gt=0)
    request_id:str=Field(min_length=8,max_length=64)
    note:str=Field(min_length=5,max_length=240)
    @field_validator('request_id','note')
    @classmethod
    def meaningful(cls,v):
        if not v.strip():raise ValueError('内容不可为空白')
        return v.strip()
