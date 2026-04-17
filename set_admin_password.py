from dotenv import load_dotenv
load_dotenv()
from backend.database import SessionLocal, User
from backend.routers.auth import _hash_password

db = SessionLocal()
u = db.query(User).filter(User.phone == '0801111111').first()
u.password_hash = _hash_password('admin1234')
db.commit()
print('done')
db.close()
