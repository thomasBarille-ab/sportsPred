from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass
class FixtureDTO:
    external_id: str
    sport: str          # 'ligue1' | 'nba'
    home_team_id: str
    home_team_name: str
    away_team_id: str
    away_team_name: str
    match_date: datetime
    season: str
    competition: str
    status: str = "scheduled"
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    round: Optional[int] = None


@dataclass
class ResultDTO:
    external_id: str
    sport: str
    home_score: int
    away_score: int
    status: str         # 'FINISHED' | 'FINAL' etc.


class DataProvider(ABC):
    """Interface commune à tous les providers de données.

    Pour ajouter un nouveau provider (ex: API-Football payant) :
      1. Créer une sous-classe de DataProvider
      2. Implémenter les 3 méthodes abstraites
      3. Passer la nouvelle instance aux jobs via Settings
    """

    @abstractmethod
    def fetch_upcoming_fixtures(self, from_date: date, to_date: date) -> list[FixtureDTO]:
        """Matchs à venir dans la fenêtre [from_date, to_date]."""
        ...

    @abstractmethod
    def fetch_recent_results(self, from_date: date, to_date: date) -> list[ResultDTO]:
        """Résultats terminés dans la fenêtre [from_date, to_date]."""
        ...

    @abstractmethod
    def fetch_season_fixtures(self, season: str) -> list[FixtureDTO]:
        """Tous les matchs (joués + planifiés) pour une saison donnée.
        Utilisé pour le chargement historique initial.
        """
        ...
