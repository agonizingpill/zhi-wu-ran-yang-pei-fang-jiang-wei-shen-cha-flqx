from datetime import date
import math,random
from src.fulfillment_models import DyeLot,DyeIssue,ColorDispatch,ShadeComplaint
from sqlalchemy import select,func
from src.database import Base,engine,Session
from src.models import User,DyeRecipe,Swatch,Trial
from src.production_models import RecipeRevision,DyeBatch
from src.auth import hash_password

def seed():
    Base.metadata.create_all(engine)
    with Session() as db:
        for role in ['admin','colorist','viewer']:
            email=f'{role}@qise.test'
            if not db.scalar(select(User).where(User.email==email)):db.add(User(email=email,password=hash_password('Dye2026!'),role=role))
        recipes=[('DY-021','靛蓝亚麻',1.8,.4,.2),('DY-022','苔绿棉麻',.3,1.7,.7),('DY-023','赤陶平纹',.8,.3,1.5)]
        for code,name,a,b,c in recipes:
            if not db.scalar(select(DyeRecipe).where(DyeRecipe.code==code)):db.add(DyeRecipe(code=code,name=name,dye_a=a,dye_b=b,dye_c=c))
        db.flush();rr=db.scalars(select(DyeRecipe).order_by(DyeRecipe.id)).all();rng=random.Random(42)
        for i in range(60):
            lot=['REF-021','SAMPLE-021','SAMPLE-OFF'][i] if i<3 else f'SW-{i:03}'
            if db.scalar(select(Swatch).where(Swatch.lot==lot)):continue
            group=0 if i<3 else i%3
            l,a,b=(48,-2,-25) if i==0 else (48.7,-1.5,-24.5) if i==1 else (62,8,-14) if i==2 else (48+group*9+rng.uniform(-2,2),-2+group*6+rng.uniform(-1,1),-25+group*15+rng.uniform(-1,1))
            spectrum=[round(min(.95,max(.03,.26+group*.11+.1*math.sin(j/5+group)+rng.gauss(0,.012))),5) for j in range(36)]
            db.add(Swatch(recipe_id=rr[group].id,lot=lot,spectrum=spectrum,l=l,a=a,b=b,measured=True))
        prod=db.scalar(select(DyeRecipe).where(DyeRecipe.code=='DY-PROD'))
        if not prod:
            prod=DyeRecipe(code='DY-PROD',name='量产靛蓝基准',dye_a=1.8,dye_b=.4,dye_c=.2,locked=True);db.add(prod);db.flush()
        for lot,l,a,b in [('PROD-REF',48,-2,-25),('PROD-OK',48.5,-1.8,-24.8)]:
            if not db.scalar(select(Swatch).where(Swatch.lot==lot)):db.add(Swatch(recipe_id=prod.id,lot=lot,spectrum=[.3]*36,l=l,a=a,b=b))
        db.flush();ref=db.scalar(select(Swatch).where(Swatch.lot=='PROD-REF'));candidate=db.scalar(select(Swatch).where(Swatch.lot=='PROD-OK'))
        trial=db.scalar(select(Trial).where(Trial.recipe_id==prod.id,Trial.state=='approved'))
        if not trial:
            trial=Trial(recipe_id=prod.id,reference_swatch_id=ref.id,candidate_swatch_id=candidate.id,delta_e=.574456,state='approved',note='量产演示基准');db.add(trial);db.flush()
        if not db.scalar(select(DyeBatch).where(DyeBatch.code=='DB-DEMO-001')):db.add(DyeBatch(code='DB-DEMO-001',approved_trial_id=trial.id,kg=250,target_snapshot={'lot':ref.lot,'recipe_id':prod.id,'l':ref.l,'a':ref.a,'b':ref.b,'trial_delta_e':trial.delta_e}))
        for code,component,kg,expiry,state in [('DYE-A-1117','A',80,date(2027,12,31),'active'),('DYE-B-1117','B',40,date(2027,12,31),'active'),('DYE-C-1117','C',20,date(2027,12,31),'active'),('DYE-C-HOLD','C',5,date(2027,12,31),'quarantine'),('DYE-B-OLD','B',5,date(2026,1,1),'active')]:
            if not db.scalar(select(DyeLot).where(DyeLot.code==code)):db.add(DyeLot(code=code,component=component,available_kg=kg,expires_on=expiry,state=state))
        _seed_swap_scene(db,trial,prod)
        db.commit()
        print({name:db.scalar(select(func.count()).select_from(model)) for name,model in [('users',User),('recipes',DyeRecipe),('swatches',Swatch)]})

SWAP_LOTS=[('DYE-SW-A1','A',40),('DYE-SW-A2','A',40),('DYE-SW-A3','A',40),
           ('DYE-SW-B1','B',40),('DYE-SW-B2','B',40),('DYE-SW-C1','C',40),('DYE-SW-C2','C',40)]
SWAP_CLAIMED=[('A1','B1','C1'),('A1','B1','C1'),('A2','B2','C2'),('A2','B2','C2'),
              ('A3','B1','C2'),('A3','B2','C1'),('A1','B2','C2'),('A2','B1','C1'),
              ('A3','B1','C1'),('A1','B2','C1'),('A2','B2','C1'),('A1','B1','C2')]
SWAP_EXCHANGED={6:8,8:6}

def _seed_swap_scene(db,trial,recipe):
    """Twelve 200kg lab tanks; tank 7 and tank 9 have their weighing labels swapped.

    Every tank's measured Lab is generated from the material *actually* in the
    tank (dye-lot strength effect plus deterministic noise), while its issue
    credentials claim the mislabelled buckets. A retry keeps existing rows.
    """
    rng=random.Random(7);base=(48.,-2.,-25.)
    effect={'A1':(0,0,0),'A2':(1.15,-.55,.75),'A3':(-.95,.85,-.65),
            'B1':(0,0,0),'B2':(.75,.45,-.85),'C1':(0,0,0),'C2':(-.65,-.95,.55)}
    existing=db.scalar(select(DyeBatch).where(DyeBatch.code=='DB-SWAP-01'))
    if existing:return
    lots={}
    for code,component,kg in SWAP_LOTS:
        lot=db.scalar(select(DyeLot).where(DyeLot.code==code))
        if not lot:
            lot=DyeLot(code=code,component=component,available_kg=kg,expires_on=date(2027,12,31),state='active');db.add(lot);db.flush()
        lots[lot.code]=lot
    snapshot={k:getattr(recipe,k) for k in ['id','code','name','dye_a','dye_b','dye_c']}
    for index,claimed in enumerate(SWAP_CLAIMED):
        code=f'DB-SWAP-{index+1:02d}';actual=SWAP_CLAIMED[SWAP_EXCHANGED[index]] if index in SWAP_EXCHANGED else claimed
        lab=[round(base[k]+sum(effect[c][k] for c in actual)+rng.gauss(0,.15),2) for k in range(3)]
        batch=DyeBatch(code=code,approved_trial_id=trial.id,kg=200,
            target_snapshot={'lot':'PROD-REF','recipe_id':recipe.id,'l':base[0],'a':base[1],'b':base[2],
                             'trial_delta_e':trial.delta_e,'recipe_snapshot':snapshot},
            lab_l=lab[0],lab_a=lab[1],lab_b=lab[2],state='qualified')
        batch.delta_e=sum((lab[k]-base[k])**2 for k in range(3))**.5
        db.add(batch);db.flush()
        for component,alias in zip('ABC',claimed):
            lot=lots[f'DYE-SW-{alias}'];kg=round(snapshot['dye_'+component.lower()]*batch.kg/100,6)
            lot.available_kg=round(lot.available_kg-kg,6)
            db.add(DyeIssue(batch_id=batch.id,lot_id=lot.id,component=component,kg=kg,
                recipe_snapshot=snapshot,issued_by='colorist@qise.test'))
if __name__=='__main__':seed()
