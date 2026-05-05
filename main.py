from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from datetime import datetime, timedelta

# --- DATABASE SETUP ---
SQLALCHEMY_DATABASE_URL = "sqlite:///./amanet.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# --- THE TABLES ---
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    nume = Column(String)
    rol = Column(String)


class AmanetItem(Base):
    __tablename__ = "obiecte_amanet"
    id = Column(Integer, primary_key=True, index=True)
    nume_client = Column(String, nullable=True)  # <--- ADDED: To allow searching by name
    nume = Column(String)
    categorie = Column(String)
    valoare_piata = Column(Float)
    suma_imprumutata = Column(Float)
    comision_zilnic = Column(Float, default=0.003)
    status = Column(String, default="amanetat")  # statusuri: amanetat, prelungit, de_vanzare, vandut, returnat, rezervat
    data_start_comision = Column(String)
    data_scadenta = Column(String)
    rezervat_de_id = Column(Integer, nullable=True)


class Tranzactie(Base):
    __tablename__ = "tranzactii"
    id = Column(Integer, primary_key=True, index=True)
    obiect_id = Column(Integer)
    user_id = Column(Integer)
    nume_client = Column(String, nullable=True)
    tip_tranzactie = Column(String)
    suma = Column(Float)
    data = Column(String, default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


Base.metadata.create_all(bind=engine)

# --- APP SETUP ---
app = FastAPI()
from fastapi.staticfiles import StaticFiles

app.mount("/", StaticFiles(directory="static", html=True), name="static")

# Permitem accesul din browser (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- ROUTES ---

@app.post("/adauga_user")
def create_user(nume: str, rol: str, db: Session = Depends(get_db)):
    nou_user = User(nume=nume, rol=rol)
    db.add(nou_user)
    db.commit()
    db.refresh(nou_user)
    return nou_user


@app.post("/evaluare_si_amanetare")
def amaneteaza_obiect(nume: str, categorie: str, valoare_piata: float, user_id: int, nume_client: str,
                      zile_contract: int = 30, db: Session = Depends(get_db)):
    suma_imprumutata = round(valoare_piata * 0.50, 2)
    azi = datetime.now()
    scadenta = azi + timedelta(days=zile_contract)

    nou_obiect = AmanetItem(
        nume_client=nume_client, # <--- ADDED: Saves the name directly to the item
        nume=nume,
        categorie=categorie,
        valoare_piata=valoare_piata,
        suma_imprumutata=suma_imprumutata,
        data_start_comision=azi.strftime("%Y-%m-%d"),
        data_scadenta=scadenta.strftime("%Y-%m-%d"),
        status="amanetat"
    )
    db.add(nou_obiect)
    db.commit()
    db.refresh(nou_obiect)

    tranzactie = Tranzactie(obiect_id=nou_obiect.id, user_id=user_id, nume_client=nume_client,
                            tip_tranzactie="imprumut", suma=-suma_imprumutata)
    db.add(tranzactie)
    db.commit()

    return {"mesaj": "Obiect amanetat!", "suma_oferita_clientului": suma_imprumutata,
            "data_scadenta": nou_obiect.data_scadenta}


@app.get("/situatie_contract/{obiect_id}")
def calculeaza_datorie(obiect_id: int, db: Session = Depends(get_db)):
    obiect = db.query(AmanetItem).filter(AmanetItem.id == obiect_id).first()
    if not obiect or obiect.status not in ["amanetat", "prelungit"]:
        return {"eroare": "Contract invalid sau inchis."}

    data_start = datetime.strptime(obiect.data_start_comision, "%Y-%m-%d")
    zile_trecute = (datetime.now() - data_start).days
    zile_trecute = max(1, zile_trecute)

    dobanda = round(obiect.suma_imprumutata * obiect.comision_zilnic * zile_trecute, 2)
    total_de_plata = obiect.suma_imprumutata + dobanda

    return {
        "zile_trecute": zile_trecute,
        "suma_imprumutata": obiect.suma_imprumutata,
        "dobanda_acumulata": dobanda,
        "total_de_plata": total_de_plata
    }


@app.post("/prelungire_contract/{obiect_id}")
def prelungeste_contract(obiect_id: int, user_id: int, zile_extra: int = 30, db: Session = Depends(get_db)):
    situatie = calculeaza_datorie(obiect_id, db)
    if "eroare" in situatie: return situatie

    obiect = db.query(AmanetItem).filter(AmanetItem.id == obiect_id).first()
    dobanda_platita = situatie["dobanda_acumulata"]

    obiect.status = "prelungit"
    obiect.data_start_comision = datetime.now().strftime("%Y-%m-%d")
    noua_scadenta = datetime.now() + timedelta(days=zile_extra)
    obiect.data_scadenta = noua_scadenta.strftime("%Y-%m-%d")

    tranzactie = Tranzactie(obiect_id=obiect_id, user_id=user_id, tip_tranzactie="prelungire", suma=dobanda_platita)
    db.add(tranzactie)
    db.commit()

    return {"mesaj": f"Contract prelungit. S-a incasat dobanda de {dobanda_platita} RON.",
            "noua_scadenta": obiect.data_scadenta}


@app.post("/restituire_obiect/{obiect_id}")
def restituie_obiect(obiect_id: int, user_id: int, db: Session = Depends(get_db)):
    situatie = calculeaza_datorie(obiect_id, db)
    if "eroare" in situatie: return situatie

    obiect = db.query(AmanetItem).filter(AmanetItem.id == obiect_id).first()
    total_incasat = situatie["total_de_plata"]

    obiect.status = "returnat"

    tranzactie = Tranzactie(obiect_id=obiect_id, user_id=user_id, tip_tranzactie="restituire", suma=total_incasat)
    db.add(tranzactie)
    db.commit()

    return {"mesaj": "Obiect returnat clientului!", "suma_incasata": total_incasat}


@app.get("/obiecte")
def get_toate_obiectele(db: Session = Depends(get_db)):
    return db.query(AmanetItem).all()


# RUTA NOUA: Pentru cautare client dupa nume (utilizat in staff.html)
@app.get("/cauta_client/{nume_cautat}")
def cauta_client(nume_cautat: str, db: Session = Depends(get_db)):
    # .ilike() makes the search case-insensitive and the % act as wildcards
    obiecte = db.query(AmanetItem).filter(AmanetItem.nume_client.ilike(f"%{nume_cautat}%")).all()
    if not obiecte:
        return {"eroare": "Nu s-a gasit niciun contract pentru acest nume."}
    return obiecte


@app.post("/rezervare/{obiect_id}")
def rezerva_obiect(obiect_id: int, user_id: int, db: Session = Depends(get_db)):
    obiect = db.query(AmanetItem).filter(AmanetItem.id == obiect_id).first()
    if not obiect:
        return {"eroare": "Obiectul nu exista."}

    obiect.status = "rezervat"
    obiect.rezervat_de_id = user_id
    db.commit()
    return {"mesaj": "Rezervare confirmata!"}


@app.get("/alerte_confiscare")
def get_alerte_confiscare(db: Session = Depends(get_db)):
    azi = datetime.now()
    # 5 zile de gratie
    limita_gratie = (azi - timedelta(days=5)).strftime("%Y-%m-%d")

    obiecte_expirate = db.query(AmanetItem).filter(
        AmanetItem.data_scadenta <= limita_gratie,
        AmanetItem.status.in_(["amanetat", "prelungit"])
    ).all()

    for obj in obiecte_expirate:
        obj.status = "de_vanzare"
    db.commit()

    return {"obiecte_trecute_la_vanzare": obiecte_expirate}


@app.get("/schimb_valutar")
def calculeaza_schimb(suma: float, valuta: str, directie: str = "to_ron"):
    curs = {"EUR": 4.97, "USD": 4.65, "GBP": 5.80, "CHF": 5.10, "RSD": 0.042, "MDL": 0.26, "CAD": 3.45, "AUD": 3.05,
            "JPY": 0.031, "BGN": 2.54, "HUF": 0.013, "TRY": 0.14}
    v = valuta.upper()
    if v not in curs: return {"eroare": "Valuta nesuportata."}

    if directie == "from_ron":
        total = round(suma / curs[v], 2)
        mesaj = f"{suma} RON = {total} {v}"
    else:
        total = round(suma * curs[v], 2)
        mesaj = f"{suma} {v} = {total} RON"
    return {"mesaj_rezultat": mesaj, "curs_folosit": curs[v]}