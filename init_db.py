from app import app, db, Race, Result

with app.app_context():
    db.create_all()

    db.session.add(race1)
    db.session.commit()
