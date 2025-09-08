import datetime as dt
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import (create_engine, Column, Integer, String, Date, Time,
                        DateTime, ForeignKey, UniqueConstraint)
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, Session

DB_URL = "sqlite:///hospital.db"
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

# ---------- MODELS ----------
class Patient(Base):
    __tablename__ = "patients"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    age = Column(Integer)
    gender = Column(String)
    symptoms = Column(String)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

class Doctor(Base):
    __tablename__ = "doctors"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    gender = Column(String)
    department = Column(String, nullable=False)
    days = Column(String, nullable=False)  # "Mon,Tue,Wed"
    start_time = Column(String, nullable=False)  # "09:00"
    end_time = Column(String, nullable=False)    # "13:00"
    slot_minutes = Column(Integer, default=15)
    appointments = relationship("Appointment", back_populates="doctor")

class Appointment(Base):
    __tablename__ = "appointments"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    appt_date = Column(Date, nullable=False)
    appt_time = Column(Time, nullable=False)
    token_no = Column(Integer, nullable=False)
    status = Column(String, default="booked")
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    patient = relationship("Patient")
    doctor = relationship("Doctor", back_populates="appointments")
    __table_args__ = (UniqueConstraint("doctor_id", "appt_date", "appt_time", name="uniq_doctor_slot"),)

Base.metadata.create_all(engine)

def seed_doctors():
    db = SessionLocal()
    if db.query(Doctor).count() == 0:
        docs = [
            # General
            Doctor(name="Dr. Anil", gender="M", department="General RMP",
                   days="Mon,Tue,Wed,Thu,Fri,Sat", start_time="09:00", end_time="12:00", slot_minutes=10),
            Doctor(name="Dr. Meera", gender="F", department="General RMP",
                   days="Mon,Wed,Fri", start_time="15:00", end_time="18:00", slot_minutes=10),
            # Dentist
            Doctor(name="Dr. Priya", gender="F", department="Dentist",
                   days="Tue,Thu,Sat", start_time="10:00", end_time="13:00", slot_minutes=15),
            # Physiologist
            Doctor(name="Dr. Arvind", gender="M", department="Physiologist",
                   days="Tue,Thu", start_time="14:00", end_time="17:00", slot_minutes=20),
        ]
        db.add_all(docs); db.commit()
    db.close()
seed_doctors()

# ---------- SCHEMAS ----------
class PatientIn(BaseModel):
    name: str
    phone: str
    age: Optional[int] = None
    gender: Optional[str] = None
    symptoms: Optional[str] = None

class PatientOut(PatientIn):
    id: int
    created_at: dt.datetime

class AvailabilityIn(BaseModel):
    department: str
    date: str  # YYYY-MM-DD

class AppointmentIn(BaseModel):
    patient_id: int
    doctor_id: int
    date: str   # YYYY-MM-DD
    time: str   # HH:MM

class AppointmentOut(BaseModel):
    id: int
    patient_id: int
    doctor_id: int
    appt_date: dt.date
    appt_time: dt.time
    token_no: int
    status: str

# ---------- UTILS ----------
def weekday_name(d: dt.date) -> str:
    return ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][d.weekday()]

def time_grid(start: dt.time, end: dt.time, step_min: int):
    cur = dt.datetime.combine(dt.date.today(), start)
    end_dt = dt.datetime.combine(dt.date.today(), end)
    step = dt.timedelta(minutes=step_min)
    out = []
    while cur <= end_dt:
        out.append(cur.time())
        cur += step
    return out

# ---------- APP ----------
app = FastAPI(title="Hospital Booking API")

@app.post("/patients", response_model=PatientOut)
def create_patient(body: PatientIn):
    db: Session = SessionLocal()
    p = Patient(**body.dict())
    db.add(p); db.commit(); db.refresh(p); db.close()
    return PatientOut(id=p.id, name=p.name, phone=p.phone, age=p.age, gender=p.gender,
                      symptoms=p.symptoms, created_at=p.created_at)

@app.post("/availability")
def availability(body: AvailabilityIn):
    date = dt.date.fromisoformat(body.date)
    w = weekday_name(date)
    db: Session = SessionLocal()
    docs = db.query(Doctor).filter(Doctor.department == body.department).all()
    out = []
    for d in docs:
        if w not in d.days.split(","):
            continue
        start = dt.time.fromisoformat(d.start_time)
        end = dt.time.fromisoformat(d.end_time)
        slots = time_grid(start, end, d.slot_minutes)
        taken = {a.appt_time for a in db.query(Appointment).filter(
            Appointment.doctor_id == d.id, Appointment.appt_date == date).all()}
        free = [t.strftime("%H:%M") for t in slots if t not in taken]
        out.append({"doctor_id": d.id, "doctor_name": d.name, "free_slots": free})
    db.close()
    return {"date": body.date, "department": body.department, "availability": out}

@app.post("/appointments", response_model=AppointmentOut)
def book(body: AppointmentIn):
    date = dt.date.fromisoformat(body.date)
    time = dt.time.fromisoformat(body.time)
    db: Session = SessionLocal()
    # sanity checks
    patient = db.get(Patient, body.patient_id)
    doctor = db.get(Doctor, body.doctor_id)
    if not patient: db.close(); raise HTTPException(404, "Patient not found")
    if not doctor: db.close(); raise HTTPException(404, "Doctor not found")
    if weekday_name(date) not in doctor.days.split(","):
        db.close(); raise HTTPException(400, "Doctor not available that day")
    # slot validity
    start = dt.time.fromisoformat(doctor.start_time)
    end = dt.time.fromisoformat(doctor.end_time)
    if time not in time_grid(start, end, doctor.slot_minutes):
        db.close(); raise HTTPException(400, "Invalid time slot")
    # already taken?
    clash = db.query(Appointment).filter_by(doctor_id=doctor.id, appt_date=date, appt_time=time).first()
    if clash: db.close(); raise HTTPException(409, "Slot already booked")
    # token = count + 1
    token = db.query(Appointment).filter_by(doctor_id=doctor.id, appt_date=date).count() + 1
    appt = Appointment(patient_id=patient.id, doctor_id=doctor.id,
                       appt_date=date, appt_time=time, token_no=token)
    db.add(appt); db.commit(); db.refresh(appt); db.close()
    return AppointmentOut(id=appt.id, patient_id=appt.patient_id, doctor_id=appt.doctor_id,
                          appt_date=appt.appt_date, appt_time=appt.appt_time,
                          token_no=appt.token_no, status=appt.status)
