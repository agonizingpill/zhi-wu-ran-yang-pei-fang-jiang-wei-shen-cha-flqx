"""Supervisor-confirmed exchange of material attribution between two tanks.

A mislabelled weighing bucket leaves stock balances correct but hangs one
bucket's material on the wrong tank. The original ``DyeIssue`` credentials are
never rewritten: confirming a correction only records that the two tanks'
complete A/B/C credential sets are exchanged. Stock balances stay untouched.

``effective_issues`` derives the attribution used by the release guard and the
weighing page; ``overview`` exposes the whole-tank-holdout model and the scored
candidate pairs, recommending a pair only when the evidence supports it.
"""
from sqlalchemy import select,or_
from fastapi import HTTPException
from .models import Trial,DyeRecipe
from .production_models import DyeBatch
from .fulfillment_models import DyeIssue,DyeLot
from .correction_models import IssueCorrection
from .correction_ml import evaluate,model_summary,score_pair
from .recipe_service import log

def _recipe_code(db,batch):
    snapshot=(batch.target_snapshot or {}).get('recipe_snapshot') or {}
    if snapshot.get('code'):return snapshot['code']
    trial=db.get(Trial,batch.approved_trial_id) if batch.approved_trial_id else None
    recipe=db.get(DyeRecipe,trial.recipe_id) if trial else None
    return recipe.code if recipe else ''

def _rows(db,batch_id):
    return db.scalars(select(DyeIssue).where(DyeIssue.batch_id==batch_id).order_by(DyeIssue.component)).all()

def _correction_for(db,batch_id):
    return db.scalar(select(IssueCorrection).where(
        or_(IssueCorrection.batch_low_id==batch_id,IssueCorrection.batch_high_id==batch_id)))

def _source_id(correction,batch_id):
    if not correction:return batch_id
    return correction.batch_high_id if correction.batch_low_id==batch_id else correction.batch_low_id

def effective_issues(db,batch_id,codes=None):
    """A/B/C credential rows currently attributed to the tank.

    An exchanged pair points at the partner's untouched original rows, so the
    original credentials and stock balances never change.
    """
    correction=_correction_for(db,batch_id);source=_source_id(correction,batch_id)
    if codes is None:codes={x.id:x.code for x in db.scalars(select(DyeLot))}
    return [{'component':row.component,'lot_id':row.lot_id,'lot_code':codes.get(row.lot_id,''),
             'kg':row.kg,'issued_by':row.issued_by,'source_batch_id':source}
            for row in _rows(db,source)]

def guarded_by_correction(db,batch):
    """A corrected tank already owns a complete credential set; weighing again
    would add a duplicate bucket that is not part of the exchanged pair."""
    correction=_correction_for(db,batch.id)
    if correction:raise HTTPException(409,'该缸用料归属已纠正，不能追加称料')

def _attribution(rows,lots):
    return {component:{'component':component,'lot_id':row.lot_id,'lot_code':lots[row.lot_id].code if row.lot_id in lots else '','kg':row.kg,'issued_by':row.issued_by,'issued_at':row.issued_at.isoformat() if row.issued_at else None} for component,row in rows.items()}

def _records(db,corrections):
    lots={x.id:x for x in db.scalars(select(DyeLot))}
    partner={}
    for correction in corrections:
        partner[correction.batch_low_id]=correction.batch_high_id
        partner[correction.batch_high_id]=correction.batch_low_id
    codes={b.id:b.code for b in db.scalars(select(DyeBatch))}
    rows_cache={};records=[]
    for batch in db.scalars(select(DyeBatch).order_by(DyeBatch.id)):
        correction=next((c for c in corrections if batch.id in (c.batch_low_id,c.batch_high_id)),None)
        source=_source_id(correction,batch.id)
        if source not in rows_cache:rows_cache[source]={row.component:row for row in _rows(db,source)}
        rows=rows_cache[source]
        complete=set(rows)=={'A','B','C'} and all(row.lot_id in lots for row in rows.values())
        lab=[batch.lab_l,batch.lab_a,batch.lab_b]
        measured=all(v is not None for v in lab)
        concentrations=None
        if complete:
            snapshot=rows['A'].recipe_snapshot or {}
            values=[float(snapshot.get('dye_'+c.lower(),0)) for c in 'ABC']
            if all(values):concentrations=values
        structured=bool(complete and concentrations is not None)
        records.append({'id':batch.id,'code':batch.code,'kg':batch.kg,'state':batch.state,
            'lab':lab,'concentrations':concentrations,
            'lots':{c:(lots[rows[c].lot_id].code if structured else '') for c in 'ABC'},
            'complete':bool(structured and measured),'measured':measured,
            'released':batch.state=='released','corrected':correction is not None,
            'partner_code':codes.get(partner.get(batch.id),''),
            'correctable':bool(structured and measured and batch.state!='released' and correction is None),
            'recipe_code':_recipe_code(db,batch)})
    return records

def _all_corrections(db):
    return db.scalars(select(IssueCorrection).order_by(IssueCorrection.id.desc())).all()

def overview(db):
    corrections=_all_corrections(db)
    records=_records(db,corrections)
    result=evaluate(records)
    by_id={r['id']:r for r in records}
    result['corrections']=[{'id':c.id,'batch_low_id':c.batch_low_id,'batch_high_id':c.batch_high_id,
        'low_code':by_id.get(c.batch_low_id,{}).get('code',''),
        'high_code':by_id.get(c.batch_high_id,{}).get('code',''),
        'state':c.state,'request_id':c.request_id,'confirmed_by':c.confirmed_by,
        'created_at':c.created_at,'evidence':c.evidence,
        'original_snapshot':c.original_snapshot,'applied_snapshot':c.applied_snapshot}
        for c in corrections]
    attribution=[]
    lots={x.id:x.code for x in db.scalars(select(DyeLot))}
    for batch in db.scalars(select(DyeBatch).order_by(DyeBatch.id)):
        correction=_correction_for(db,batch.id)
        attribution.append({'batch_id':batch.id,'code':batch.code,
            'partner_id':_source_id(correction,batch.id) if correction else None,
            'corrected':correction is not None,'issues':effective_issues(db,batch.id,lots)})
    result['attribution']=attribution
    return result

def _pair(db,low_id,high_id,corrections):
    records=_records(db,corrections)
    by_id={r['id']:r for r in records}
    if low_id not in by_id or high_id not in by_id:raise HTTPException(404,'生产缸次不存在')
    low,high=sorted([by_id[low_id],by_id[high_id]],key=lambda r:r['id'])
    return score_pair(records,low,high,model_summary(records))

def _replay(existing,low_id,high_id):
    if {existing.batch_low_id,existing.batch_high_id}!={low_id,high_id}:
        raise HTTPException(409,'请求号已用于其他缸对，请重新提交')
    return existing

def _result(correction):
    return {'id':correction.id,'batch_low_id':correction.batch_low_id,
            'batch_high_id':correction.batch_high_id,'state':correction.state,
            'request_id':correction.request_id,'confirmed_by':correction.confirmed_by,
            'created_at':correction.created_at,'evidence':correction.evidence,
            'original_snapshot':correction.original_snapshot,
            'applied_snapshot':correction.applied_snapshot}

def confirm(db,data,user):
    low_id,high_id=sorted([data.batch_low_id,data.batch_high_id])
    if low_id==high_id:raise HTTPException(409,'交换必须涉及两个不同生产缸次')
    # A retry after an interrupted response carries the same request id and
    # returns the applied record instead of exchanging a second time.
    existing=db.scalar(select(IssueCorrection).where(IssueCorrection.request_id==data.request_id))
    if existing:return _result(_replay(existing,low_id,high_id))
    # Lock both tanks in id order, the order the whole-order issue path uses,
    # so a concurrent release of either tank serialises with the exchange.
    tanks=db.scalars(select(DyeBatch).where(DyeBatch.id.in_([low_id,high_id]))
                     .order_by(DyeBatch.id).with_for_update()).all()
    if len(tanks)!=2:raise HTTPException(404,'生产缸次不存在')
    low,high=tanks
    existing=db.scalar(select(IssueCorrection).where(IssueCorrection.request_id==data.request_id))
    if existing:return _result(_replay(existing,low_id,high_id))
    if low.state=='released' or high.state=='released':
        raise HTTPException(409,'缸对中已有缸次先签放，整对退回：未交换任何用料归属')
    if db.scalar(select(IssueCorrection).where(or_(
            IssueCorrection.batch_low_id.in_([low_id,high_id]),
            IssueCorrection.batch_high_id.in_([low_id,high_id])))):
        raise HTTPException(409,'所选缸次已参与其他用料归属纠正，不能再次交换')
    low_rows={r.component:r for r in _rows(db,low_id)}
    high_rows={r.component:r for r in _rows(db,high_id)}
    if set(low_rows)!={'A','B','C'} or set(high_rows)!={'A','B','C'}:
        raise HTTPException(409,'两缸均须具备完整 A/B/C 原始称料凭据')
    if any(batch.lab_l is None or batch.lab_a is None or batch.lab_b is None for batch in (low,high)):
        raise HTTPException(409,'两缸均须登记生产实测色值后才能比对预测误差')
    corrections=_all_corrections(db)
    candidate=_pair(db,low_id,high_id,corrections)
    if not candidate['recommended']:
        raise HTTPException(409,'证据不足，不推荐交换：'+candidate['reason'])
    lots={x.id:x for x in db.scalars(select(DyeLot))}
    model=model_summary(_records(db,corrections))
    correction=IssueCorrection(batch_low_id=low_id,batch_high_id=high_id,state='applied',
        request_id=data.request_id,confirmed_by=user.email,
        evidence={'note':data.note,'pair':candidate,
                  'model':{k:v for k,v in model.items() if k!='cv'},
                  'method':'整缸留出：除本对后训练，互换前后各算一次预测误差'},
        original_snapshot={'low':_attribution(low_rows,lots),'high':_attribution(high_rows,lots),
                           'low_batch':{'code':low.code,'kg':low.kg},'high_batch':{'code':high.code,'kg':high.kg}},
        applied_snapshot={'low':_attribution(high_rows,lots),'high':_attribution(low_rows,lots),
                          'low_batch':{'code':low.code,'kg':low.kg},'high_batch':{'code':high.code,'kg':high.kg},
                          'swapped':True})
    db.add(correction)
    log(db,user,f'纠正用料归属 {low.code} ⇄ {high.code}（整对交换，库存与原始凭据不变）')
    db.commit();return _result(correction)

def release_note(db,batch):
    """Attribution note recorded when a tank is released after a correction."""
    correction=_correction_for(db,batch.id)
    if not correction:return ''
    partner=db.get(DyeBatch,_source_id(correction,batch.id))
    return f'用料归属已纠正，按 {partner.code if partner else "对调缸"} 的 A/B/C 凭据审签'
