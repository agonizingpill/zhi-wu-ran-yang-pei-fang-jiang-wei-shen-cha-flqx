"""Whole-tank-holdout cross validation and swap-evidence scoring.

A tank's claimed material attribution is the A/B/C concentration on its issue
credentials plus the identity of the three dye lots actually weighed. The target
is the tank's measured CIELAB. A small ridge regression learns, from historical
tanks, how lot strength and recipe basis move the measured colour.

Training and validation always hold out whole tanks, never individual rows.
Model quality is a leave-one-tank-out CV over every tank with complete
credentials and measurements. A suspected pair is scored with a model refitted
without either tank, so both the "current attribution" and the "swapped
attribution" errors are genuine whole-tank holdouts. A pair is recommended only
when the exchange clearly explains both measured colours (both errors fall, the
total falls at least 40%, and no residual exceeds 1.5 ΔE). Otherwise the pair is
reported as insufficient evidence and must not be acted on.

Pure Python/numpy so it can be tested without the API or a database.
"""
from itertools import combinations
import numpy as np

MIN_TRAIN=6
CV_MEDIAN_LIMIT=1.5
MIN_ORIGINAL_ERROR=1.5
MIN_IMPROVEMENT=1.0
SWAP_GAIN_RATIO=.6
MAX_SWAPPED_ERROR=1.5
MAX_CANDIDATES=12
RIDGE=.5


def _layouts(rows):
    lots=[]
    for row in rows:
        for component in 'ABC':
            key=(component,row['lots'].get(component))
            if key not in lots:lots.append(key)
    return lots


def _design(rows,lots):
    columns=[]
    for row in rows:
        indicators=[]
        for component,lot_code in lots:
            indicators.append(1. if row['lots'].get(component)==lot_code else 0.)
        columns.append([1.,*[float(v) for v in row['concentrations']],*indicators])
    return np.asarray(columns,dtype=float)


def fit(rows):
    lots=_layouts(rows)
    x=_design(rows,lots)
    y=np.asarray([row['lab'] for row in rows],dtype=float)
    penalty=np.eye(x.shape[1])*RIDGE;penalty[0,0]=0.
    normal=x.T@x+penalty
    try:weights=np.linalg.solve(normal,x.T@y)
    except np.linalg.LinAlgError:weights=np.linalg.lstsq(normal,x.T@y,rcond=None)[0]
    return {'lots':lots,'weights':weights}


def predict(model,rows):
    return _design(rows,model['lots'])@model['weights']


def distance(left,right):
    return float(np.sqrt(np.sum((np.asarray(left)-np.asarray(right))**2)))


def cross_validation(rows):
    if len(rows)<4:return None
    errors=[]
    for index,row in enumerate(rows):
        rest=rows[:index]+rows[index+1:]
        if len(rest)<3:continue
        predicted=predict(fit(rest),[row])[0]
        errors.append({'batch_id':row['id'],'code':row['code'],'delta_e':round(distance(predicted,row['lab']),3)})
    if not errors:return None
    values=np.asarray([x['delta_e'] for x in errors],dtype=float)
    return {'folds':len(errors),'median_delta_e':round(float(np.median(values)),3),
            'mean_delta_e':round(float(np.mean(values)),3),'max_delta_e':round(float(np.max(values)),3),
            'errors':errors}


def model_summary(rows):
    complete=[r for r in rows if r.get('complete')]
    cv=cross_validation(complete)
    ready=bool(cv and len(complete)>=MIN_TRAIN and cv['median_delta_e']<=CV_MEDIAN_LIMIT)
    return {'ready':ready,'trained':len(complete),'features':'称料凭据：A/B/C 浓度 + 实际染料批次',
            'target':'实测 CIELAB（L*/a*/b*）','holdout':'整缸留出（leave-one-tank-out）',
            'cv':cv,'min_train':MIN_TRAIN,'cv_median_limit':CV_MEDIAN_LIMIT,
            'thresholds':{'min_original_error':MIN_ORIGINAL_ERROR,'min_improvement':MIN_IMPROVEMENT,
                          'swap_gain_ratio':SWAP_GAIN_RATIO,'max_swapped_error':MAX_SWAPPED_ERROR}}


def _public(row):
    return {'id':row['id'],'code':row['code'],'kg':row['kg'],'state':row['state'],'lab':row['lab'],
            'lots':row.get('lots',{}),'recipe_code':row.get('recipe_code',''),
            'corrected':row.get('corrected',False)}


def _same_attribution(low,high):
    return (all(abs(a-b)<1e-9 for a,b in zip(low['concentrations'],high['concentrations']))
            and low['lots']==high['lots'])


def score_pair(rows,low,high,model=None):
    model=model or model_summary(rows)
    train=[r for r in rows if r.get('complete') and r['id'] not in (low['id'],high['id'])]
    if not low.get('correctable') or not high.get('correctable'):
        return _candidate(low,high,None,False,'缸次须同属未签放、凭据完整、已实测色值且未纠正')
    if low.get('recipe_code')!=high.get('recipe_code'):
        return _candidate(low,high,None,False,'两缸配方不同，整对分量不可比，证据不足')
    if _same_attribution(low,high):
        return _candidate(low,high,None,False,'两缸用料归属完全相同，互换在模型上不可辨识，证据不足')
    if len(train)<MIN_TRAIN:
        return _candidate(low,high,None,False,
            f'训练样本不足（除本对后仅 {len(train)} 缸，需至少 {MIN_TRAIN} 缸完整凭据）')
    if not model['ready']:
        cv=model['cv']
        detail='无法计算' if not cv else f"中位 ΔE {cv['median_delta_e']:.3f}"
        return _candidate(low,high,None,False,f'整缸留出误差{detail}，未达 {CV_MEDIAN_LIMIT:.1f}，模型不足以判断')
    predicted=predict(fit(train),[low,high])
    original={'low':distance(predicted[0],low['lab']),'high':distance(predicted[1],high['lab'])}
    swapped={'low':distance(predicted[1],low['lab']),'high':distance(predicted[0],high['lab'])}
    original_total=original['low']+original['high'];swapped_total=swapped['low']+swapped['high']
    improvement=original_total-swapped_total
    recommended=(
        min(original.values())>=MIN_ORIGINAL_ERROR and
        swapped['low']<original['low'] and swapped['high']<original['high'] and
        max(swapped.values())<=MAX_SWAPPED_ERROR and
        improvement>=MIN_IMPROVEMENT and swapped_total<=SWAP_GAIN_RATIO*original_total
    )
    if recommended:reason='互换后两缸预测误差同时下降，残差均在门限内'
    elif min(original.values())<MIN_ORIGINAL_ERROR:reason='原归属下至少一缸预测误差很小，互换依据不足'
    elif max(swapped.values())>MAX_SWAPPED_ERROR:reason=f'互换后仍有 ΔE 超过 {MAX_SWAPPED_ERROR:.1f} 的缸，证据不足'
    else:reason=f'互换后误差下降不足（合计 {original_total:.3f} → {swapped_total:.3f}）'
    return _candidate(low,high,{
        'original':{k:round(v,3) for k,v in original.items()},
        'swapped':{k:round(v,3) for k,v in swapped.items()},
        'original_total':round(original_total,3),'swapped_total':round(swapped_total,3),
        'improvement':round(improvement,3)},recommended,reason)


def evaluate(rows):
    model=model_summary(rows)
    eligible=sorted([r for r in rows if r.get('correctable')],key=lambda r:r['id'])
    candidates=[score_pair(rows,low,high,model) for low,high in combinations(eligible,2)]
    recommended=[c for c in candidates if c['recommended']]
    scored=[c for c in candidates if c['evidence'] and not c['recommended']]
    scored.sort(key=lambda c:c['evidence']['original_total'],reverse=True)
    unexplained=[c for c in candidates if not c['evidence']]
    return {'model':model,'candidates':(recommended+scored+unexplained)[:max(MAX_CANDIDATES,len(recommended))],
            'recommended_count':len(recommended),'scored_pairs':len(candidates)}


def _candidate(low,high,evidence,recommended,reason):
    return {'batch_low':_public(low),'batch_high':_public(high),'evidence':evidence,
            'recommended':bool(recommended),'reason':reason}
