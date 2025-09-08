# app.py
import os
import datetime as dt
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import (
    create_engine, Column, Integer, String, Date, Time, DateTime,
    ForeignKey, UniqueConstraint, inspect, text, or_
)
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, Session

# ---------------- DB SETUP ----------------
DB_URL = os.getenv("DATABASE_URL", "sqlite:///hospital.db")
engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ---------------- MODELS ----------------
class Patient(Base):
    __tablename__ = "patients"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    alt_phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
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
    days = Column(String, nullable=False)  # "Mon,Tue,Wed,Thu,Fri"
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
    status = Column(String, default="booked")  # booked | cancelled
    payment_method = Column(String, default="direct")  # direct | insurance
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    patient = relationship("Patient")
    doctor = relationship("Doctor", back_populates="appointments")
    __table_args__ = (UniqueConstraint("doctor_id", "appt_date", "appt_time", name="uniq_doctor_slot"),)

Base.metadata.create_all(engine)

# --- simple auto-migrations for new columns (safe for SQLite/Postgres) ---
def ensure_columns():
    insp = inspect(engine)
    with engine.begin() as conn:
        # patients: alt_phone, email
        cols = {c['name'] for c in insp.get_columns("patients")}
        if "alt_phone" not in cols:
            conn.execute(text("ALTER TABLE patients ADD COLUMN alt_phone VARCHAR"))
        if "email" not in cols:
            conn.execute(text("ALTER TABLE patients ADD COLUMN email VARCHAR"))
        # appointments: payment_method
        cols = {c['name'] for c in insp.get_columns("appointments")}
        if "payment_method" not in cols:
            conn.execute(text("ALTER TABLE appointments ADD COLUMN payment_method VARCHAR DEFAULT 'direct'"))

ensure_columns()

# ---------------- SEED DOCTORS ----------------
def seed_doctors():
    db = SessionLocal()
    if db.query(Doctor).count() == 0:
        docs = [
            # General Medicine
            Doctor(name="Dr. Anil Kumar", gender="M", department="General Medicine",
                   days="Mon,Tue,Wed,Thu,Fri,Sat", start_time="09:00", end_time="12:00", slot_minutes=10),
            Doctor(name="Dr. Meera Nair", gender="F", department="General Medicine",
                   days="Mon,Wed,Fri", start_time="15:00", end_time="18:00", slot_minutes=10),
            # Cardiology
            Doctor(name="Dr. Shalini", gender="F", department="Cardiology",
                   days="Mon,Thu", start_time="10:00", end_time="13:00", slot_minutes=20),
            Doctor(name="Dr. Manish", gender="M", department="Cardiology",
                   days="Tue,Fri", start_time="10:00", end_time="13:00", slot_minutes=20),
            # Orthopedics
            Doctor(name="Dr. Varun Iyer", gender="M", department="Orthopedics",
                   days="Wed,Sat", start_time="10:00", end_time="13:00", slot_minutes=15),
            # Dentistry
            Doctor(name="Dr. Priya Menon", gender="F", department="Dentistry",
                   days="Tue,Thu,Sat", start_time="10:00", end_time="13:00", slot_minutes=15),
            # Neurology
            Doctor(name="Dr. Venkatesh", gender="M", department="Neurology",
                   days="Mon,Wed", start_time="15:00", end_time="18:00", slot_minutes=20),
        ]
        db.add_all(docs)
        db.commit()
    db.close()
seed_doctors()

# ---------------- UTILS ----------------
def weekday_name(date: dt.date) -> str:
    return ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][date.weekday()]

def time_range(start: dt.time, end: dt.time, step_min: int):
    cur = dt.datetime.combine(dt.date.today(), start)
    end_dt = dt.datetime.combine(dt.date.today(), end)
    delta = dt.timedelta(minutes=step_min)
    while cur <= end_dt:
        yield cur.time()
        cur += delta

def appt_code(appt_id: int) -> str:
    return f"APPT-{appt_id:06d}"

# ---------------- SCHEMAS ----------------
class PatientIn(BaseModel):
    name: str
    phone: str
    alt_phone: Optional[str] = None
    email: Optional[EmailStr] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    symptoms: Optional[str] = None

    @field_validator("phone")
    @classmethod
    def phone_clean(cls, v):
        return v.strip()

class PatientOut(PatientIn):
    id: int
    created_at: dt.datetime

class DoctorOut(BaseModel):
    id: int
    name: str
    gender: Optional[str]
    department: str
    days: str
    start_time: str
    end_time: str
    slot_minutes: int

class AvailabilityQuery(BaseModel):
    department: str
    date: str  # YYYY-MM-DD

class AppointmentIn(BaseModel):
    patient_id: int
    doctor_id: int
    date: str        # YYYY-MM-DD
    time: str        # HH:MM
    payment_method: Optional[str] = "direct"  # direct | insurance

class AppointmentOut(BaseModel):
    id: int
    appointment_code: str
    patient_id: int
    doctor_id: int
    appt_date: dt.date
    appt_time: dt.time
    token_no: int
    status: str
    payment_method: str

class RescheduleIn(BaseModel):
    appointment_id: int
    date: str       # YYYY-MM-DD
    time: str       # HH:MM
    new_doctor_id: Optional[int] = None  # optional change of doctor

class CancelIn(BaseModel):
    appointment_id: int

# ---------------- APP ----------------
app = FastAPI(title="A1 Hospital Booking API", version="1.2")

# CORS (allow all origins; tighten in production if needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # set to your domain(s) in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"ok": True, "time": dt.datetime.utcnow().isoformat()}

# -------- DOCTORS --------
@app.get("/doctors", response_model=List[DoctorOut])
def list_doctors(department: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(Doctor)
    if department:
        q = q.filter(Doctor.department == department)
    return [DoctorOut(**{
        "id": d.id, "name": d.name, "gender": d.gender, "department": d.department,
        "days": d.days, "start_time": d.start_time, "end_time": d.end_time, "slot_minutes": d.slot_minutes
    }) for d in q.all()]

# -------- PATIENTS (CREATE + READ) --------
@app.post("/patients", response_model=PatientOut)
def create_patient(body: PatientIn, db: Session = Depends(get_db)):
    p = Patient(**body.dict())
    db.add(p)
    db.commit()
    db.refresh(p)
    return PatientOut(**{
        "id": p.id, "name": p.name, "phone": p.phone, "alt_phone": p.alt_phone, "email": p.email,
        "age": p.age, "gender": p.gender, "symptoms": p.symptoms, "created_at": p.created_at
    })

@app.get("/patients/{patient_id}", response_model=PatientOut)
def get_patient(patient_id: int, db: Session = Depends(get_db)):
    p = db.get(Patient, patient_id)
    if not p:
        raise HTTPException(404, "Patient not found")
    return PatientOut(
        id=p.id, name=p.name, phone=p.phone, alt_phone=p.alt_phone, email=p.email,
        age=p.age, gender=p.gender, symptoms=p.symptoms, created_at=p.created_at
    )

@app.get("/patients")
def list_patients(q: Optional[str] = None, limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    query = db.query(Patient)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Patient.name.ilike(like), Patient.phone.ilike(like)))
    rows = query.order_by(Patient.id.desc()).offset(offset).limit(limit).all()
    return [
        {
            "id": p.id, "name": p.name, "phone": p.phone, "alt_phone": p.alt_phone,
            "email": p.email, "age": p.age, "gender": p.gender, "created_at": p.created_at
        }
        for p in rows
    ]

@app.get("/patients/lookup")
def lookup_patient(phone: str = Query(..., description="10-digit phone"), db: Session = Depends(get_db)):
    p = db.query(Patient).filter(Patient.phone == phone.strip()).first()
    if not p:
        raise HTTPException(404, "No patient with that phone")
    return {"id": p.id, "name": p.name, "phone": p.phone, "email": p.email}

# -------- AVAILABILITY --------
@app.post("/availability")
def availability(q: AvailabilityQuery, db: Session = Depends(get_db)):
    date = dt.date.fromisoformat(q.date)
    w = weekday_name(date)
    docs = db.query(Doctor).filter(Doctor.department == q.department).all()
    out = []
    for d in docs:
        if w not in d.days.split(","):
            continue
        start = dt.time.fromisoformat(d.start_time)
        end = dt.time.fromisoformat(d.end_time)
        slots = list(time_range(start, end, d.slot_minutes))
        taken = {a.appt_time for a in db.query(Appointment)
                 .filter(Appointment.doctor_id == d.id, Appointment.appt_date == date).all()}
        free = [t.strftime("%H:%M") for t in slots if t not in taken]
        out.append({"doctor_id": d.id, "doctor_name": d.name, "free_slots": free})
    return {"date": q.date, "department": q.department, "availability": out}

# -------- APPOINTMENTS (BOOK / READ / RESCHEDULE / CANCEL) --------
@app.post("/appointments", response_model=AppointmentOut)
def book_appointment(body: AppointmentIn, db: Session = Depends(get_db)):
    patient = db.get(Patient, body.patient_id)
    if not patient:
        raise HTTPException(404, "Patient not found")
    doctor = db.get(Doctor, body.doctor_id)
    if not doctor:
        raise HTTPException(404, "Doctor not found")

    date = dt.date.fromisoformat(body.date)
    w = weekday_name(date)
    if w not in doctor.days.split(","):
        raise HTTPException(400, f"Doctor not available on {w}")

    start = dt.time.fromisoformat(doctor.start_time)
    end = dt.time.fromisoformat(doctor.end_time)
    step = doctor.slot_minutes
    valid_slots = set(time_range(start, end, step))
    time = dt.time.fromisoformat(body.time)
    if time not in valid_slots:
        raise HTTPException(400, "Invalid time slot")

    existing = db.query(Appointment).filter_by(doctor_id=doctor.id, appt_date=date, appt_time=time).first()
    if existing:
        raise HTTPException(409, "Slot already booked")

    token = db.query(Appointment).filter_by(doctor_id=doctor.id, appt_date=date).count() + 1
    appt = Appointment(
        patient_id=patient.id, doctor_id=doctor.id,
        appt_date=date, appt_time=time, token_no=token,
        status="booked", payment_method=(body.payment_method or "direct")
    )
    db.add(appt)
    db.commit()
    db.refresh(appt)
    return AppointmentOut(
        id=appt.id,
        appointment_code=appt_code(appt.id),
        patient_id=appt.patient_id, doctor_id=appt.doctor_id,
        appt_date=appt.appt_date, appt_time=appt.appt_time,
        token_no=appt.token_no, status=appt.status, payment_method=appt.payment_method
    )

@app.get("/appointments")
def list_appointments(patient_id: Optional[int] = None, doctor_id: Optional[int] = None,
                      date: Optional[str] = None, limit: int = 100, db: Session = Depends(get_db)):
    q = db.query(Appointment)
    if patient_id: q = q.filter(Appointment.patient_id == patient_id)
    if doctor_id: q = q.filter(Appointment.doctor_id == doctor_id)
    if date: q = q.filter(Appointment.appt_date == dt.date.fromisoformat(date))
    rows = q.order_by(Appointment.id.desc()).limit(limit).all()
    return [{
        "id": a.id,
        "appointment_code": appt_code(a.id),
        "patient_id": a.patient_id,
        "doctor_id": a.doctor_id,
        "date": a.appt_date.isoformat(),
        "time": a.appt_time.strftime("%H:%M"),
        "token_no": a.token_no,
        "status": a.status,
        "payment_method": a.payment_method
    } for a in rows]

@app.get("/appointments/patient/{patient_id}")
def list_patient_appointments(patient_id: int, db: Session = Depends(get_db)):
    appts = db.query(Appointment).filter(Appointment.patient_id == patient_id).all()
    return [{
        "id": a.id,
        "appointment_code": appt_code(a.id),
        "doctor": a.doctor.name,
        "department": a.doctor.department,
        "date": a.appt_date.isoformat(),
        "time": a.appt_time.strftime("%H:%M"),
        "token_no": a.token_no,
        "status": a.status,
        "payment_method": a.payment_method
    } for a in appts]

@app.get("/appointments/by_id/{appointment_id}", response_model=AppointmentOut)
def get_appointment(appointment_id: int, db: Session = Depends(get_db)):
    a = db.get(Appointment, appointment_id)
    if not a:
        raise HTTPException(404, "Appointment not found")
    return AppointmentOut(
        id=a.id,
        appointment_code=appt_code(a.id),
        patient_id=a.patient_id, doctor_id=a.doctor_id,
        appt_date=a.appt_date, appt_time=a.appt_time,
        token_no=a.token_no, status=a.status, payment_method=a.payment_method
    )

@app.post("/appointments/reschedule", response_model=AppointmentOut)
def reschedule(body: RescheduleIn, db: Session = Depends(get_db)):
    a = db.get(Appointment, body.appointment_id)
    if not a:
        raise HTTPException(404, "Appointment not found")

    # allow changing doctor if provided
    doctor = db.get(Doctor, body.new_doctor_id) if body.new_doctor_id else db.get(Doctor, a.doctor_id)
    if not doctor:
        raise HTTPException(404, "Doctor not found")

    date = dt.date.fromisoformat(body.date)
    time = dt.time.fromisoformat(body.time)
    w = weekday_name(date)
    if w not in doctor.days.split(","):
        raise HTTPException(400, f"Doctor not available on {w}")

    start = dt.time.fromisoformat(doctor.start_time)
    end = dt.time.fromisoformat(doctor.end_time)
    if time not in set(time_range(start, end, doctor.slot_minutes)):
        raise HTTPException(400, "Invalid time slot")

    clash = db.query(Appointment).filter_by(doctor_id=doctor.id, appt_date=date, appt_time=time).first()
    if clash and clash.id != a.id:
        raise HTTPException(409, "Requested slot already booked")

    a.doctor_id = doctor.id
    a.appt_date = date
    a.appt_time = time
    db.commit()
    db.refresh(a)
    return AppointmentOut(
        id=a.id,
        appointment_code=appt_code(a.id),
        patient_id=a.patient_id, doctor_id=a.doctor_id,
        appt_date=a.appt_date, appt_time=a.appt_time,
        token_no=a.token_no, status=a.status, payment_method=a.payment_method
    )

@app.post("/appointments/cancel")
def cancel(body: CancelIn, db: Session = Depends(get_db)):
    a = db.get(Appointment, body.appointment_id)
    if not a:
        raise HTTPException(404, "Appointment not found")
    a.status = "cancelled"
    db.commit()
    return {"ok": True, "appointment_id": a.id, "appointment_code": appt_code(a.id), "status": a.status}
