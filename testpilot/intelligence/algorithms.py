"""Детерминированные оценки и ограниченный взвешенный set cover."""
import math
from statistics import median
from testpilot.contracts import Weights

CRITICALITY={'low':.25,'medium':.5,'high':.75,'critical':1.0}
ALGORITHM='weighted-cover-1.0'

def statistics(results,defects,default_duration):
    valid=[r for r in results if r['status'] in ('passed','failed')]
    n=len(valid)
    return {'runs':n,'failure_rate':sum(r['status']=='failed' for r in valid)/n if n else .1,
            'median_duration':median(r['duration_seconds'] for r in results if r['status']!='skipped') if any(r['status']!='skipped' for r in results) else default_duration,
            'defect_frequency':len(defects)/max(n,1),'defect_severity':sum(CRITICALITY[d['severity']] for d in defects),
            'sufficient':n>=5,'risk_default':not bool(n)}

def score_test(test,covered,affected,stats,weights,predicted_risk=None):
    duration=stats['median_duration'] or test['duration_seconds']
    factors={'coverage':len(covered&affected)/len(affected) if affected else 0,
             'risk':stats['failure_rate'] if predicted_risk is None else predicted_risk,
             'defects':min(1.0,stats['defect_severity']/max(stats['runs'],1)),
             'criticality':CRITICALITY[test['criticality']], 'cost':1/(1+duration/300)}
    score=min(1.0,max(0.0,sum(weights[k]*factors[k] for k in factors)))
    return {'test_id':test['test_id'],'title':test['title'],'score':score,'factors':factors,'weights':weights,'algorithm_version':ALGORITHM,
            'statistics':stats,'duration_seconds':duration,'mandatory':test['mandatory'],'covered_objects':sorted(covered&affected),
            'scoring_method':'ml-risk' if predicted_risk is not None else 'weighted','synthetic':test['synthetic']}

def coverage_summary(rows,selected,affected):
    available=set().union(*(set(r['covered_objects']) for r in rows)) if rows else set()
    covered=set().union(*(set(r['covered_objects']) for r in rows if r['test_id'] in selected)) if selected else set()
    covered &= affected
    return {'covered_objects':sorted(covered),'uncovered_objects':sorted(affected-covered),
            'available_objects':sorted(available),'unavailable_objects':sorted(affected-available),
            'coverage_available':len(covered)/len(available) if available else 0.0,
            'coverage_all':len(covered)/len(affected) if affected else 0.0,
            'denominator_available':len(available),'denominator_all':len(affected)}

def optimize(rows,affected,budget,min_coverage,unavailable_mandatory=None):
    unavailable_mandatory=unavailable_mandatory or []
    mandatory=[r for r in rows if r['mandatory']]
    optional=sorted([r for r in rows if not r['mandatory'] and r['covered_objects']],key=lambda r:r['test_id'])
    selected={r['test_id'] for r in mandatory}
    mandatory_time=sum(r['duration_seconds'] for r in mandatory)
    all_available=set().union(*(set(r['covered_objects']) for r in rows)) if rows else set()
    conflicts=[]
    method='exact' if len(optional)<=18 else 'greedy'
    if unavailable_mandatory: conflicts.append({'code':'mandatory_unavailable','test_ids':unavailable_mandatory})
    if mandatory_time>budget:
        conflicts.append({'code':'mandatory_over_budget','excess_seconds':mandatory_time-budget})
    else:
        base_covered=set().union(*(set(r['covered_objects']) for r in mandatory)) if mandatory else set()
        if method=='exact':
            # До 2^18 подмножеств; битовая маска покрытия исключает двойной счёт.
            bit={o:1<<i for i,o in enumerate(sorted(all_available))}
            masks=[sum(bit[o] for o in r['covered_objects']) for r in optional]
            initial=sum(bit[o] for o in base_covered)
            best=(-1,-1.0,0.0);best_ids=()
            def search(i,cost,mask,utility,ids):
                nonlocal best,best_ids
                if cost>budget-mandatory_time+1e-9:return
                if i==len(optional):
                    key=(mask.bit_count(),round(utility,12),-round(cost,9))
                    if key>best or (key==best and ids<best_ids): best=key;best_ids=ids
                    return
                search(i+1,cost,mask,utility,ids)
                r=optional[i]
                search(i+1,cost+r['duration_seconds'],mask|masks[i],utility+r['score'],ids+(r['test_id'],))
            search(0,0,initial,0,())
            selected.update(best_ids)
        else:
            remaining=budget-mandatory_time; covered=set(base_covered);pending=list(optional)
            while pending:
                pending.sort(key=lambda r:(-len(set(r['covered_objects'])-covered)/max(r['duration_seconds'],.001),-r['score']/max(r['duration_seconds'],.001),r['test_id']))
                r=pending.pop(0)
                if r['duration_seconds']<=remaining and set(r['covered_objects'])-covered:
                    selected.add(r['test_id']);remaining-=r['duration_seconds'];covered.update(r['covered_objects'])
    summary=coverage_summary(rows,selected,affected)
    if summary['coverage_available']+1e-9<min_coverage:
        conflicts.append({'code':'coverage_unreachable','required':min_coverage,'achieved':summary['coverage_available']})
    if not affected: conflicts.append({'code':'empty_impact'})
    elif not all_available: conflicts.append({'code':'no_confirmed_coverage'})
    order=sorted(rows,key=lambda r:(-r['score'],r['test_id']))
    for r in order:
        r['selected']=r['test_id'] in selected
        r['explanation']=('Обязательный тест' if r['mandatory'] else 'Выбран оптимизатором по покрытию и приоритету') if r['selected'] else ('Нет подтверждённого покрытия зоны' if not r['covered_objects'] else 'Не вошёл в оптимальный набор при данном бюджете')
    return {**summary,'selected_ids':[r['test_id'] for r in order if r['selected']], 'ranked_tests':order,
            'duration_seconds':sum(r['duration_seconds'] for r in rows if r['test_id'] in selected),'full_duration_seconds':sum(r['duration_seconds'] for r in rows),
            'budget_seconds':budget,'min_coverage':min_coverage,'status':'conflict' if conflicts else 'feasible','conflicts':conflicts,
            'optimization_method':method,'algorithm_version':ALGORITHM,'warnings':['Жадный алгоритм не гарантирует глобальный оптимум.'] if method=='greedy' else []}
