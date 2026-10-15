"""Логистическая регрессия; параметры в JSON, без небезопасного pickle."""
import math
from collections import defaultdict
from itertools import groupby
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, balanced_accuracy_score
from testpilot.intelligence.algorithms import CRITICALITY

FEATURES=['past_runs_scaled','past_failure_rate','criticality','declared_duration_scaled']
MIN_ROWS=100
MIN_AUC=.65
MIN_BALANCED_ACCURACY=.55

def features(test,past):
    n=len(past)
    return [min(n,100)/100,sum(past)/n if n else .1,CRITICALITY[test['criticality']],min(test['duration_seconds'],3600)/3600]

def train(tests,events):
    by_id={t['test_id']:t for t in tests};past=defaultdict(list);x=[];y=[];times=[]
    valid=sorted([r for r in events if r['status'] in ('passed','failed') and r['test_id'] in by_id],key=lambda r:(r['started_at'],r['test_id']))
    # Признаки используют только результаты, завершённые СТРОГО до начала события.
    for r in valid:
        history=[int(z['status']=='failed') for z in valid if z['test_id']==r['test_id'] and z['finished_at']<r['started_at']]
        x.append(features(by_id[r['test_id']],history));y.append(int(r['status']=='failed'));times.append(r['started_at'])
    info={'feature_names':FEATURES,'rows':len(y),'min_rows':MIN_ROWS,'auc_threshold':MIN_AUC,'balanced_accuracy_threshold':MIN_BALANCED_ACCURACY,'algorithm':'logistic-regression-1.0'}
    if len(y)<MIN_ROWS: return {**info,'accepted':False,'reason':'Недостаточно истории (нужно 100 завершённых passed/failed)'}
    split=int(len(y)*.8)
    # Одинаковое время не пересекает границу train/validation.
    while split>0 and split<len(y) and times[split-1]==times[split]: split-=1
    if len(set(y[:split]))<2 or len(set(y[split:]))<2 or split<50:
        return {**info,'accepted':False,'reason':'Нужны оба класса в обучении и отложенной временной выборке'}
    model=LogisticRegression(random_state=42,max_iter=1000,class_weight='balanced').fit(x[:split],y[:split])
    probabilities=model.predict_proba(x[split:])[:,1]
    auc=roc_auc_score(y[split:],probabilities);balanced=balanced_accuracy_score(y[split:],probabilities>=.5)
    accepted=auc>=MIN_AUC and balanced>=MIN_BALANCED_ACCURACY
    return {**info,'accepted':bool(accepted),'reason':'Модель допущена по синтетической/имеющейся истории' if accepted else 'Качество ниже установленного порога; используется базовая оценка',
            'auc':float(auc),'balanced_accuracy':float(balanced),'train_rows':split,'validation_rows':len(y)-split,
            'train_end':times[split-1],'validation_start':times[split], 'coefficients':model.coef_[0].tolist(),'intercept':float(model.intercept_[0])}

def predict(model,test,results):
    if not model or not model.get('accepted'): return None
    history=[int(r['status']=='failed') for r in results if r['status'] in ('passed','failed')]
    z=model['intercept']+sum(c*f for c,f in zip(model['coefficients'],features(test,history)))
    return 1/(1+math.exp(-max(-700,min(700,z))))
