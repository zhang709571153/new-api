"""Summarize existing collectors only; no network request or resident probe."""
import argparse
from collections import defaultdict
import datetime as dt
import json
from pathlib import Path


def parse(value): return dt.datetime.fromisoformat(value.replace('Z', '+00:00'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--since', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--health-root', default=r'C:\srv\realyu-observability\evidence')
    parser.add_argument('--path-observer', default=r'C:\ProgramData\RealYuTunnelGuard\runtime\path-observer.jsonl')
    args = parser.parse_args()
    start = parse(args.since); now = dt.datetime.now(dt.timezone.utc)
    paths = defaultdict(list); timestamps = []; malformed = 0
    day = start.astimezone(dt.timezone.utc).date()
    while day <= now.date():
        file = Path(args.health_root) / ('samples-' + day.strftime('%Y%m%d') + '.jsonl')
        if file.exists():
            with file.open(encoding='utf-8-sig') as stream:
                for line in stream:
                    try: item = json.loads(line); at = parse(item['timestamp'])
                    except (ValueError, KeyError): malformed += 1; continue
                    if start <= at <= now:
                        timestamps.append(at)
                        for row in item['samples']:
                            paths[row['target']].append({'at':at.isoformat(), **{k:row.get(k) for k in
                              ('http_status','curl_exit','healthy','category','ready_connections','identity_verified','probe_id','cf_ray')}})
        day += dt.timedelta(days=1)
    summaries = {}
    for name, rows in paths.items():
        failures = [row for row in rows if not row['healthy']]
        summaries[name] = {'healthy':len(rows)-len(failures), 'total':len(rows),
            'success_fraction':(len(rows)-len(failures))/len(rows),
            'first_failure':failures[0] if failures else None, 'failures':failures, 'last':rows[-1]}
    path_rows = []
    with Path(args.path_observer).open(encoding='utf-8-sig') as stream:
        for line in stream:
            try: row=json.loads(line); at=parse(row['at'])
            except (ValueError, KeyError): continue
            if start <= at <= now: path_rows.append(row)
    def eight_sg(row):
        flows=row.get('connections',[])
        return row.get('ok') is True and len(flows)==8 and all(flow.get('chains')==['RealYu-SG-HY2','REALYU'] for flow in flows)
    def max_gap(times): return max(((b-a).total_seconds() for a,b in zip(times,times[1:])),default=None)
    deltas=[]
    if len(path_rows)>=2:
        before={flow['id']:flow for flow in path_rows[-2]['connections']}
        for flow in path_rows[-1]['connections']:
            old=before.get(flow['id'])
            if old: deltas.append({'same_flow':True,'upload_delta':flow['upload']-old['upload'],'download_delta':flow['download']-old['download']})
    result={'at':now.isoformat(),'requested_since':start.isoformat(), 'new_probes':0,'model_calls':0,
      'health':summaries, 'health_first':timestamps[0].isoformat() if timestamps else None,
      'health_last':timestamps[-1].isoformat() if timestamps else None,
      'health_latest_age_seconds':(now-timestamps[-1]).total_seconds() if timestamps else None,
      'max_health_interval_seconds':max_gap(timestamps),'malformed_health_lines':malformed,
      'path':{'sample_count':len(path_rows),'all_eight_fixed_sg':bool(path_rows) and all(map(eight_sg,path_rows)),
              'first':path_rows[0]['at'] if path_rows else None,'last':path_rows[-1]['at'] if path_rows else None,
              'last_age_seconds':(now-parse(path_rows[-1]['at'])).total_seconds() if path_rows else None,
              'max_interval_seconds':max_gap([parse(r['at']) for r in path_rows]),
              'latest_two_same_flow_deltas':deltas},
      'limits':['Existing complete-response health contracts, not model inference.',
                'No new public exit or socket-owner probe; exact path based on existing observer.',
                'Sampling bounds outage timing; this window alone cannot establish or disprove a recurring 20-minute fault or long-term SLA.']}
    with Path(args.output).open('x',encoding='utf-8') as stream: json.dump(result,stream,indent=2)
    print(json.dumps({'paths':{k:f"{v['healthy']}/{v['total']}" for k,v in summaries.items()},
        'last_age':result['health_latest_age_seconds'],'sg':result['path']['all_eight_fixed_sg'],'path_samples':len(path_rows)}))


if __name__=='__main__': main()
