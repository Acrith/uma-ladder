from app import app, db, Race, Result

with app.app_context():
    db.create_all()

    race1 = Race(
        season="Entry Cup #1", week=1, race_number=1, race_type="Sprint",
        race_name="Centaur Stakes (Trainers Cup)", grade="G2", event_type="Normal",
        distance="1200m", location="Hanshin", surface="Turf",
        direction="Right", mood="Great", weather="Random"
    )

    db.session.add(race1)
    db.session.commit()

    # Add a sample result
    result1 = Result(
        race_id=race1.id,
        player_name="Acrith",
        uma_name="Gold Ship",
        placement=1,
        points=10
    )

    db.session.add(result1)
    db.session.commit()
