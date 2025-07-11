from app import app, db, Race, Result

with app.app_context():
    db.create_all()

    race1 = Race(
        season="Entry Cup #1", week=1, race_number=1, race_type="Sprint",
        race_name="Centaur Stakes (Trainers Cup)", grade="G2", event_type="Normal",
        distance="1200m", location="Hanshin", surface="Turf",
        direction="Right", mood="Great", weather="Random", participant_count="12"
    )

    db.session.add(race1)
    db.session.commit()
