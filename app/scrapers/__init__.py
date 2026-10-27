from .coops import COOPS, MAKLER
from .marktde import MarktDe
from .dresdden import Dresdden
from .immobiliende import ImmobilienDe
from .immoscout_mail import ImmoScoutMail
from .immowelt import Immowelt
from .kleinanzeigen import Kleinanzeigen
from .ohnemakler import OhneMakler
from .wggesucht import WgGesucht

SCRAPERS = {s.name: s for s in (Immowelt, Kleinanzeigen, OhneMakler, WgGesucht, ImmobilienDe, ImmoScoutMail, Dresdden, *COOPS, *MAKLER, MarktDe)}
