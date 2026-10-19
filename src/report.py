"""Create reproducible replay and randomized-pilot summaries using stdlib only."""
import csv, json, math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def read_csv(p):
    with p.open(encoding='utf-8-sig', newline='') as f: return list(csv.DictReader(f))
def read_jsonl(p):
    with p.open(encoding='utf-8') as f: return [json.loads(x) for x in f if x.strip()]
def wilson(successes, n, z=1.96):
    if not n: return [None,None]
    p=successes/n; den=1+z*z/n
    centre=(p+z*z/(2*n))/den
    half=z*math.sqrt((p*(1-p)+z*z/(4*n))/n)/den
    return [round(max(0,centre-half),4),round(min(1,centre+half),4)]
def main():
    decisions=read_jsonl(ROOT/'sample_run'/'decisions.jsonl')
    actions=read_jsonl(ROOT/'sample_run'/'actions.jsonl')
    lat=json.loads((ROOT/'sample_run'/'latency_report.json').read_text())
    goal_outcomes=read_jsonl(ROOT/'sample_run'/'goal_outcomes.jsonl') if (ROOT/'sample_run'/'goal_outcomes.jsonl').exists() else []
    outcomes=read_csv(ROOT/'pilot'/'pilot_outcomes.csv')
    by={}
    for arm in sorted(set(r['arm'] for r in outcomes)):
        rows=[r for r in outcomes if r['arm']==arm]
        n=len(rows); success=sum(r['succeeded'].lower()=='true' for r in rows)
        served=sum(r['served'].lower()=='true' for r in rows)
        by[arm]={'assigned_users':n,'successes':success,'success_rate':round(success/n,4) if n else None,
                 'success_rate_wilson_95_ci':wilson(success,n),'served_users':served,
                 'intention_to_treat_denominator':'all assigned users'}
    arm_names=list(by)
    diff=None; se=None; ci=None
    if 'treatment' in by and 'holdout' in by:
        a,b=by['treatment'],by['holdout']; diff=a['success_rate']-b['success_rate']
        se=math.sqrt(a['success_rate']*(1-a['success_rate'])/a['assigned_users']+b['success_rate']*(1-b['success_rate'])/b['assigned_users'])
        ci=[round(diff-1.96*se,4),round(diff+1.96*se,4)]
    report={'replay':{'decisions':len(decisions),'actions':len(actions),'goal_outcomes':{k:sum(1 for o in goal_outcomes if o.get('outcome')==k) for k in ['succeeded','failed','inconclusive']},'action_types':{},
                      'no_action_reasons':{},'latency':lat},
            'pilot_randomized_itt':{'arms':by,'treatment_minus_holdout_risk_difference':round(diff,4) if diff is not None else None,
                                    'approx_95_ci_for_difference':ci,
                                    'interpretation':'Descriptive pilot estimate; confidence interval is a normal approximation. Assignment arm, not served status, defines the denominator.'}}
    from collections import Counter
    report['replay']['action_types']=dict(Counter(a.get('type','unknown') for a in actions))
    report['replay']['no_action_reasons']=dict(Counter(d.get('no_action_reason') for d in decisions if d.get('action')=='NO_ACTION'))
    (ROOT/'sample_run'/'final_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    lines=['# Replay and Pilot Report','', '## Replay',f"- Decisions: {len(decisions)}",f"- Published actions: {len(actions)}",f"- Goal outcomes: {report['replay']['goal_outcomes']}",f"- Action types: {report['replay']['action_types']}",f"- Latency: `{json.dumps(lat,sort_keys=True)}`",'', '## Earlier randomized pilot: intention-to-treat','']
    for arm,v in by.items(): lines.append(f"- **{arm}**: {v['successes']}/{v['assigned_users']} successes ({v['success_rate']:.1%}); 95% Wilson interval {v['success_rate_wilson_95_ci']}; served {v['served_users']}.")
    if diff is not None: lines += ['',f"- Treatment minus holdout success-rate difference: **{diff:+.1%}** (approximate 95% CI {ci[0]:+.1%} to {ci[1]:+.1%})."]
    lines += ['', 'The earlier pilot is a descriptive estimate, not proof of causal effect. Keep every randomized user in the assigned arm, including treatment users who were not served. Comparing served users with everyone else is biased because service is a post-assignment selection. The confidence interval above uses a normal approximation.', '', 'Goal outcomes are replay judgments under the configured event history and rule implementation. They are not proof of real-world causal impact. The randomized pilot figures below are from the supplied earlier pilot CSV, not from this replay run.']
    (ROOT/'sample_run'/'FINAL_REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('Wrote sample_run/final_report.json and sample_run/FINAL_REPORT.md')
if __name__=='__main__': main()
