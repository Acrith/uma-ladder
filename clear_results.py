from app import app, db, Result

with app.app_context():
    num_deleted = Result.query.delete()
    db.session.commit()
    print(f"✅ Cleared {num_deleted} result(s) from database.")