"""Load fictional development fixtures once, without fabricating real releases."""
import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid5, NAMESPACE_URL
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.config import settings
from app.core_models import User, Tenant, Membership, Property, Fact, FactRevision, Asset
from app.business_models import Inquiry, Lead, Proposal
from app.security import hash_password, context_for_user

ROOT = Path(__file__).resolve().parents[1]


def uid(name):
    return str(uuid5(NAMESPACE_URL, 'housignal-local-fixture/' + name))


def main():
    if settings.app_env != 'development':
        raise RuntimeError('Fictional fixtures are only allowed in development')
    engine = create_engine(settings.migration_database_url, hide_parameters=True)
    with Session(engine) as db:
        if db.scalar(select(User).where(User.email == settings.bootstrap_email)):
            print('Bootstrap exists; existing business data preserved')
            return
        tenant = Tenant(id=uid('tenant'), name='云境内容团队')
        owner = User(id=uid('owner'), email=settings.bootstrap_email, name='林嘉宁', password_hash=hash_password(settings.bootstrap_password))
        db.add_all([tenant, owner]); db.flush()
        db.add(Membership(id=uid('owner-membership'), user_id=owner.id, tenant_id=tenant.id, role='admin'))
        sales_users = {}
        for name in ['林顾问', '孟顾问']:
            user = User(id=uid(name), email=('lin' if name == '林顾问' else 'meng') + '@housignal.local', name=name, password_hash=hash_password(settings.bootstrap_password))
            db.add(user); db.flush()
            db.add(Membership(id=uid(name+'-membership'), user_id=user.id, tenant_id=tenant.id, role='sales'))
            sales_users[name] = user.id
        db.flush(); context_for_user(db, owner.id, tenant.id)
        fixtures = json.loads((ROOT / 'specs/seed-fixtures.json').read_text())
        for example in fixtures['projects']:
            state = example['state']; raw = state['property']; pid = uid(example['id'])
            project = Property(id=pid, tenant_id=tenant.id, name=raw['name'], city=raw['city'], property_type=raw['propertyType'], owner_name=raw['owner'], audience=raw['audience'], sales_owner_name=raw['salesOwner'], channel=state['campaign']['channel'], marketing_confirmed=raw['marketingPermission']['confirmed'], marketing_evidence=raw['marketingPermission']['evidence'], is_test=True)
            db.add(project); db.flush()
            for item in state['facts']:
                fact = Fact(id=uid(pid+item['id']), tenant_id=tenant.id, property_id=pid, semantic_key={'F01':'area','F02':'width','F03':'layout','F04':'kitchen','F05':'price','F06':'appointment_time'}[item['id']], label=item['label'], value=item['value'], unit=item['unit'], source=item['source'], status=item['status'], required=item['required'], revision=1)
                db.add(fact); db.flush()
                db.add(FactRevision(id=uid(fact.id+'1'), tenant_id=tenant.id, property_id=pid, fact_id=fact.id, revision=1, payload={'label':fact.label,'value':fact.value,'unit':fact.unit,'source':fact.source,'status':fact.status,'is_test':True}))
            for item in state['assets']:
                source = ROOT / 'specs/seed-assets' / Path(item['src']).name; key = pid + '/' + uid(pid+item['id']) + source.suffix
                destination = settings.storage_root / key; destination.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source,destination)
                db.add(Asset(id=uid(pid+item['id']), tenant_id=tenant.id, property_id=pid, name=item['name'], kind='image', storage_key=key, mime_type='image/jpeg', sha256=hashlib.sha256(source.read_bytes()).hexdigest(), byte_size=source.stat().st_size, rights='authorized', rights_evidence='仅开发环境摄影示意，不代表真实楼盘素材', origin_type='template', metadata_json={'is_test':True,'model_processing_authorized':False,'visual_label':'摄影示意'}))
            for item in state['inquiries']:
                db.add(Inquiry(id=uid(pid+item['id']), tenant_id=tenant.id, property_id=pid, name=item['name'], need=item['need'], channel=item['channel'], source_confidence=item['sourceConfidence'], source_text=item['sourceText'], contactable=item['contactable'], consent=item['consent'], qualified=item['qualified'], owner_id=sales_users.get(item['owner']), qualification_checks={'contactable':item['contactable'],'consent':item['consent'],'demand_confirmed':item['qualified'],'fit_confirmed':item['qualified']}))
            db.flush()
            for item in state['leads']:
                db.add(Lead(id=uid(pid+item['id']), tenant_id=tenant.id, property_id=pid, inquiry_id=uid(pid+item['inquiryId']), name=item['name'], need=item['need'], stage=item['stage'], owner_id=sales_users[item['owner']], next_action=item['nextAction'], source_confidence=item['sourceConfidence'], stage_history=item['stageHistory']))
            for item in state['proposals']:
                refs=[{'kind':'fact' if r.startswith('F') else 'inquiry','id':uid(pid+r),'label':r} for r in item['refs'] if r.startswith(('F','INQ'))]
                reason='已核验厨餐关系；补充本项目真实镜头后再安排创作。' if item['id']=='P02' else item['reason']
                db.add(Proposal(id=uid(pid+item['id']), tenant_id=tenant.id, property_id=pid, title=item['title'], reason=reason, status=item['status'], source_refs=refs))
        db.commit()
    print('Development account and two fictional projects created; no fake media/publications seeded')


if __name__ == '__main__':
    main()
