"""Catálogo ilustrativo y recepción de reportes comunitarios."""

from datetime import UTC, datetime
from uuid import uuid4

from repositories.noise_repository import NoiseRepository
from schemas.maps import PlaceSuggestion
from schemas.noise import Hotspot, NoiseReport, NoiseReportCreate, NoiseSource, NoiseSourceOption
from services.osm_places import PlaceSearcher, PlaceVerificationError, PlaceVerifier


NOISE_SOURCES = (
    NoiseSourceOption(value=NoiseSource.TRAFFIC, label="Tráfico vehicular y transporte público"),
    NoiseSourceOption(value=NoiseSource.COMMERCE, label="Comercio ambulante y perifoneo"),
    NoiseSourceOption(value=NoiseSource.CONSTRUCTION, label="Construcción y obras viales"),
    NoiseSourceOption(value=NoiseSource.ENTERTAINMENT, label="Música, eventos y locales nocturnos"),
    NoiseSourceOption(value=NoiseSource.OTHER, label="Otra fuente de ruido"),
)

# Datos de demostración del diseño: no representan mediciones verificadas.
# Las coordenadas son aproximadas, tomadas de OpenStreetMap (© colaboradores de OSM, ODbL).
DEMO_HOTSPOTS = (
    Hotspot(
        id="sector-01",
        name="Calle Real y Av. Giráldez",
        latitude=-12.06865,
        longitude=-75.21025,
        area="Centro Histórico",
        district="Huancayo",
        sector="Sector 01 · Centro",
        decibels=74,
        source=NoiseSource.TRAFFIC,
        source_label="Tráfico vehicular y transporte público",
        description="Congestión recurrente de microbuses y uso constante de bocinas en horas punta de la tarde.",
        time_label="Franja diurna (12 h – 19 h)",
        x=27,
        y=32,
        reference_limit=60,
    ),
    Hotspot(
        id="sector-02",
        name="Mercado Mayorista",
        latitude=-12.07304,
        longitude=-75.20502,
        area="Av. Ferrocarril",
        district="Huancayo",
        sector="Sector 02 · Comercial",
        decibels=78,
        source=NoiseSource.COMMERCE,
        source_label="Comercio ambulante y perifoneo",
        description="Altavoces comerciales promocionales y descarga de camiones en zona peatonal.",
        time_label="Franja matutina y comercial",
        x=49,
        y=49,
        reference_limit=70,
    ),
    Hotspot(
        id="sector-03",
        name="Intersección Huancavelica & Jr. Ica",
        latitude=-12.07323,
        longitude=-75.21301,
        area="Eje de Carga Urbana",
        district="Chilca",
        sector="Sector 03 · Conexión sur",
        decibels=69,
        source=NoiseSource.TRAFFIC,
        source_label="Tráfico pesado y obras viales",
        description="Circulación de transporte interprovincial y maquinaria urbana en horarios diurnos.",
        time_label="Franja diurna continua",
        x=60,
        y=55,
        reference_limit=60,
    ),
)


class NoiseService:
    def __init__(self, repository: NoiseRepository, place_verifier: PlaceVerifier,
                 place_searcher: PlaceSearcher | None = None) -> None:
        self.repository = repository
        self.place_verifier = place_verifier
        self.place_searcher = place_searcher

    def initialize(self) -> None:
        self.repository.initialize(DEMO_HOTSPOTS, NOISE_SOURCES)

    def list_hotspots(self) -> list[Hotspot]:
        return self.repository.list_hotspots()

    def list_sources(self) -> tuple[NoiseSourceOption, ...]:
        return self.repository.list_sources()

    def search_places(self, query: str) -> list[PlaceSuggestion]:
        if self.place_searcher is None:
            raise PlaceVerificationError("La búsqueda de lugares no está configurada.", 503)
        return self.place_searcher.search(query)

    def create_report(self, payload: NoiseReportCreate) -> NoiseReport:
        place = self.place_verifier.verify(payload.place_id)
        report = NoiseReport(
            **payload.model_dump(exclude={"location", "place_id"}),
            location=place.address,
            place_id=place.place_id,
            latitude=place.latitude,
            longitude=place.longitude,
            id=str(uuid4()),
            created_at=datetime.now(UTC),
        )
        return self.repository.save_report(report)
