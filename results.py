"""Team-reported PU results: immutable revisions, scoped evidence and review events."""
import base64
import io
import json
import re
from email.parser import BytesParser
from email.policy import default
from PIL import Image, UnidentifiedImageError
import access

MAX_PHOTO=2*1024*1024
MAX_BODY=3100000
PARTIES=('APC','PDP','LP','NNPP','APGA','SDP','ADC','PRP')
COUNTS=('registered','accredited','valid','rejected','cast')
LABELS=('Registered voters','Accredited voters','Valid votes','Rejected / invalid votes','Total votes cast')
SCHEMA='''CREATE TABLE IF NOT EXISTS pu_results (
 id INTEGER PRIMARY KEY,pu_id INTEGER NOT NULL REFERENCES pus(id),election TEXT NOT NULL,
 revision INTEGER NOT NULL,previous_id INTEGER REFERENCES pu_results(id),
 figures TEXT NOT NULL,parties TEXT NOT NULL,cancelled INTEGER NOT NULL,notes TEXT NOT NULL,
 submitter TEXT NOT NULL,account TEXT NOT NULL,submitted_at TEXT NOT NULL,
 correction_reason TEXT NOT NULL,flags TEXT NOT NULL,photo TEXT NOT NULL,photo_type TEXT NOT NULL,
 UNIQUE(pu_id,election,revision));
 CREATE TABLE IF NOT EXISTS result_reviews (
 id INTEGER PRIMARY KEY,result_id INTEGER NOT NULL REFERENCES pu_results(id),
 status TEXT NOT NULL,notes TEXT NOT NULL,reviewer TEXT NOT NULL,reviewed_at TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS result_reviews_result_idx ON result_reviews(result_id);
'''

def init(db):
    for statement in SCHEMA.split(';'):
        if statement.strip():db.execute(statement)

def now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f UTC')

def parse_request(content_type,body):
    """Parse bounded multipart uploads; plain URL encoding supports photo-free corrections."""
    from urllib.parse import parse_qs
    if content_type.startswith('application/x-www-form-urlencoded'):
        parsed=parse_qs(body.decode('utf-8'),keep_blank_values=True,max_num_fields=150)
        if any(len(v)!=1 for v in parsed.values()):raise ValueError('Duplicate form fields.')
        return {k:v[0] for k,v in parsed.items()},None
    if not content_type.startswith('multipart/form-data'):raise ValueError('Use a multipart result form.')
    message=BytesParser(policy=default).parsebytes(('Content-Type: '+content_type+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+body)
    if not message.is_multipart():raise ValueError('Invalid upload form.')
    fields={};photo=None;count=0
    for part in message.iter_parts():
        count+=1
        if count>150 or part.is_multipart():raise ValueError('Too many upload fields.')
        name=part.get_param('name',header='content-disposition')
        if not name or name in fields:raise ValueError('Invalid or duplicate form fields.')
        value=part.get_payload(decode=True) or b''
        if name=='photo':
            if photo is not None:raise ValueError('Upload one photo.')
            photo=value
        else:
            if len(value)>20000:raise ValueError('Form field too long.')
            fields[name]=value.decode('utf-8')
    return fields,photo

def photo_value(data):
    if not data or len(data)>MAX_PHOTO:raise ValueError('Upload a JPEG or PNG photo, at most 2 MiB.')
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ('JPEG','PNG') or image.width*image.height>20000000:raise ValueError('Use a JPEG or PNG photo of at most 20 megapixels.')
            typ='image/jpeg' if image.format=='JPEG' else 'image/png'
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError,Image.DecompressionBombWarning) as error:
        raise ValueError('The result photo is not a valid JPEG or PNG.') from error
    return base64.b64encode(data).decode('ascii'),typ

def text(data,key,maximum,required=True):
    value=data.get(key,'').strip()
    if len(value)>maximum or (required and not value):raise ValueError(f'Provide {key.replace("_"," ")} (at most {maximum} characters).')
    return value

def number(value):
    if not re.fullmatch(r'[0-9]{1,9}',value):raise ValueError('Vote counts must be whole numbers from 0 to 999999999.')
    return int(value)

def validate(data):
    figures={name:number(data.get(name,'')) for name in COUNTS}
    parties={}
    for index in range(64):
        party=data.get(f'party_{index}','').strip().upper()
        votes=data.get(f'votes_{index}','').strip()
        if not party and not votes:continue
        if not re.fullmatch(r'[A-Z][A-Z0-9 .&-]{0,39}',party) or party in parties:raise ValueError('Party names must be distinct, 1–40 characters, using letters, numbers, spaces, . & or -.')
        parties[party]=number(votes)
    if len(parties)<8:raise ValueError('Enter vote counts for at least eight parties (use 0 for a recorded zero).')
    if data.get('cancelled') not in ('yes','no'):raise ValueError('Choose the cancellation status.')
    notes=text(data,'notes',2000,False)
    if data['cancelled']=='yes' and not notes:raise ValueError('Explain the cancellation in notes.')
    flags=[]
    if sum(parties.values())!=figures['valid']:flags.append('Party votes do not equal valid votes.')
    if figures['valid']+figures['rejected']!=figures['cast']:flags.append('Valid plus rejected votes do not equal total votes cast.')
    if figures['cast']>figures['accredited']:flags.append('Total votes cast exceed accredited voters.')
    if figures['accredited']>figures['registered']:flags.append('Accredited voters exceed registered voters.')
    if data['cancelled']=='yes' and figures['cast']:flags.append('Cancelled result includes votes cast; review the notes and evidence.')
    return figures,parties,notes,flags

def identifier(value):
    value=str(value)
    if not re.fullmatch(r'[0-9]{1,19}',value) or not 0<int(value)<2**63:
        raise ValueError('Choose a valid record ID.')
    return int(value)

def get(db,result_id):
    result_id=identifier(result_id)
    row=db.execute('SELECT r.*,p.lga,p.ward,p.code,p.name FROM pu_results r JOIN pus p ON p.id=r.pu_id WHERE r.id=?',(result_id,)).fetchone()
    if not row:raise ValueError('Result does not exist.')
    access.require_scope(dict(id=row['pu_id'],lga=row['lga'],ward=row['ward']))
    return row

def save(db,data,photo):
    access.require_results_write()
    db.begin_write()
    old=get(db,data['previous_id']) if data.get('previous_id') else None
    pu_id=identifier(data.get('pu_id','0'))
    pu=db.execute('SELECT * FROM pus WHERE id=?',(pu_id,)).fetchone()
    access.require_scope(pu)
    election=text(data,'election',120)
    latest=db.execute('SELECT id,revision FROM pu_results WHERE pu_id=? AND election=? ORDER BY revision DESC LIMIT 1',(pu_id,election)).fetchone()
    if old:
        if old['pu_id']!=pu_id or old['election']!=election:raise ValueError('Corrections cannot change the polling unit or election.')
        if not latest or latest['id']!=old['id']:raise ValueError('A newer revision exists. Open the latest result before correcting it.')
        reason=text(data,'correction_reason',1000)
    else:
        if latest:raise ValueError('This PU already has a result for that election. Open it and submit a correction.')
        reason=''
    figures,parties,notes,flags=validate(data)
    encoded,typ=photo_value(photo) if photo else ((old['photo'],old['photo_type']) if old else photo_value(photo))
    user=access.current_user.get()
    record_id=db.execute('INSERT INTO pu_results(pu_id,election,revision,previous_id,figures,parties,cancelled,notes,submitter,account,submitted_at,correction_reason,flags,photo,photo_type) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id',
        (pu_id,election,old['revision']+1 if old else 1,old['id'] if old else None,json.dumps(figures),json.dumps(parties),int(data['cancelled']=='yes'),notes,text(data,'submitter',100),user['username'] if user else 'Local setup',now(),reason,json.dumps(flags),encoded,typ)).fetchone()[0]
    import app
    app.log_activity(db,'Corrected' if old else 'Submitted','PU result',record_id,'Immutable team-reported revision saved; pending verification.')
    return record_id

def review(db,data):
    access.require_admin();db.begin_write()
    row=get(db,data.get('result_id','0'))
    latest=db.execute('SELECT id FROM pu_results WHERE pu_id=? AND election=? ORDER BY revision DESC LIMIT 1',(row['pu_id'],row['election'])).fetchone()
    if latest['id']!=row['id']:raise ValueError('Only the latest revision can be reviewed.')
    status=data.get('status')
    if status not in ('Pending verification','Verified by team','Disputed'):raise ValueError('Choose a verification status.')
    notes=text(data,'review_notes',2000)
    user=access.current_user.get()
    db.execute('INSERT INTO result_reviews(result_id,status,notes,reviewer,reviewed_at) VALUES(?,?,?,?,?)',(row['id'],status,notes,user['username'] if user else 'Local setup',now()))
    import app
    app.log_activity(db,'Reviewed','PU result',row['id'],'Verification decision recorded: '+status)

def page(db,csrf,result_id=None,message=''):
    import app
    esc=app.esc
    user=access.current_user.get()
    writable=user is None or user['role'] in ('admin','coordinator','agent')
    selected=get(db,int(result_id)) if result_id else None
    pus=[dict(p) for p in db.execute('SELECT * FROM pus ORDER BY lga,ward,code') if access.in_scope(p)]
    rows=[r for r in db.execute('SELECT r.id,r.pu_id,r.election,r.revision,r.submitted_at,p.lga,p.ward,p.code FROM pu_results r JOIN pus p ON p.id=r.pu_id WHERE NOT EXISTS (SELECT 1 FROM pu_results newer WHERE newer.pu_id=r.pu_id AND newer.election=r.election AND newer.revision>r.revision) ORDER BY r.submitted_at DESC') if access.in_scope(dict(id=r['pu_id'],lga=r['lga'],ward=r['ward']))]
    body='<nav aria-label="Portal navigation"><a href="/">Dashboard</a><a href="/results" aria-current="page">PU Results</a></nav><aside>Team-reported, pending verification. These submissions are not official election results. Team verification records an internal review only.</aside><p role="status">'+esc(message)+'</p>'
    body+='<section><h2>Latest submissions</h2>'
    for row in rows:
        body+='<p><a href="/results?id='+str(row['id'])+'">'+esc(row['lga']+' / '+row['ward']+' / '+row['code']+' · '+row['election'])+'</a> · revision '+str(row['revision'])+' · '+esc(row['submitted_at'])+'</p>'
    body+=('</section>' if rows else '<p>No results submitted in your area.</p></section>')
    if selected:
        history=db.execute('SELECT id FROM pu_results WHERE pu_id=? AND election=? ORDER BY revision DESC',(selected['pu_id'],selected['election'])).fetchall()
        body+='<section><h2>'+esc(selected['code']+' · '+selected['election'])+'</h2>'
        for item in history:
            row=get(db,item['id']);figures=json.loads(row['figures']);parties=json.loads(row['parties']);flags=json.loads(row['flags'])
            reviews=db.execute('SELECT * FROM result_reviews WHERE result_id=? ORDER BY id DESC',(row['id'],)).fetchall()
            status=reviews[0]['status'] if reviews else 'Pending verification'
            body+='<details open><summary>Revision '+str(row['revision'])+' · Team-reported · '+esc(status)+'</summary><p>Submitted by '+esc(row['submitter'])+' (account: '+esc(row['account'])+') · '+esc(row['submitted_at'])+'</p>'
            body+=app.table(LABELS,[[figures[k] for k in COUNTS]])+app.table(['Party','Votes'],parties.items())
            body+='<p>Cancellation: '+('Cancelled' if row['cancelled'] else 'Not cancelled')+'</p><p>Notes: '+esc(row['notes'])+'</p><p>Correction reason: '+esc(row['correction_reason'])+'</p>'
            body+='<ul role="alert">'+''.join('<li>'+esc(flag)+'</li>' for flag in flags)+'</ul>' if flags else '<p>Arithmetic checks passed.</p>'
            body+='<a href="/results/photo?id='+str(row['id'])+'">View result-sheet photo</a>'
            body+=app.table(['Verification status','Review notes','Reviewer','Time (UTC)'],[(v['status'],v['notes'],v['reviewer'],v['reviewed_at']) for v in reviews])+'</details>'
        body+='</section>'
        if access.is_admin() and selected['id']==history[0]['id']:
            body+='<section><h2>Record verification decision</h2><form method="post" action="/results/review"><input type="hidden" name="csrf" value="'+csrf+'"><input type="hidden" name="result_id" value="'+str(selected['id'])+'"><label>Status'+app.select('status',[(s,s) for s in ('Pending verification','Verified by team','Disputed')])+'</label><label>Review notes (explain evidence and any arithmetic flags)<textarea name="review_notes" required maxlength="2000"></textarea></label><button>Save review</button></form></section>'
        if selected['id']!=history[0]['id']:
            body+='<p><a href="/results?id='+str(history[0]['id'])+'">Open latest revision to make a correction</a></p>'
            writable=False
    if writable:
        values=json.loads(selected['figures']) if selected else {}
        parties=json.loads(selected['parties']) if selected else dict.fromkeys(PARTIES,'')
        body+='<section><h2>'+('Submit a correction' if selected else 'Submit PU results')+'</h2><form method="post" action="/results" enctype="multipart/form-data"><input type="hidden" name="csrf" value="'+csrf+'">'
        if selected:
            body+='<input type="hidden" name="previous_id" value="'+str(selected['id'])+'"><input type="hidden" name="pu_id" value="'+str(selected['pu_id'])+'"><input type="hidden" name="election" value="'+esc(selected['election'])+'">'+app.field('correction_reason','Reason for correction')
        else:body+=app.location_picker(pus)+app.field('election','Election / contest and date (e.g. Council chair 2027-02-20)')
        body+=app.field('submitter','Submitter’s name',value=selected['submitter'] if selected else (user['username'] if user else ''))
        body+=''.join(app.field(k,label,'number',values.get(k,'')) for k,label in zip(COUNTS,LABELS))
        body+='<fieldset id="result-parties"><legend>Party votes (at least eight; enter 0 only for a recorded zero)</legend>'
        for index,(party,votes) in enumerate(parties.items()):
            body+='<div class="party-row">'+app.field('party_'+str(index),'Party',value=party)+app.field('votes_'+str(index),'Votes','number',votes)+'</div>'
        body+='</fieldset><button type="button" id="add-result-party">Add another party</button><label>Cancellation status'+app.select('cancelled',[('no','Not cancelled'),('yes','Cancelled')],'yes' if selected and selected['cancelled'] else 'no')+'</label><label>Cancellation / result notes<textarea name="notes" maxlength="2000">'+esc(selected['notes'] if selected else '')+'</textarea></label><label>Result-sheet photo (JPEG / PNG, max 2 MiB; max 20 megapixels)'+('<br>Leave blank to retain the previous photo.' if selected else '')+'<input type="file" name="photo" accept="image/jpeg,image/png"'+('' if selected else ' required')+'></label><p>Totals that do not add up will be flagged and saved for review. Every correction creates a new revision and resets verification.</p><button>Submit team-reported results</button></form></section>'
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PU Results · Solution 2027</title><link rel="stylesheet" href="/portal.css"></head><body class="portal-body results-body">'+app.portal_header("PU Results","Result sheets, reported figures and verification history.")+'<main>'+body+'<script src="/portal.js" defer></script></main></body></html>'
