// The league screens' words (#179), in English and Spanish. The forms follow the language picked on them; the league
// page follows the member's own language.
import type { Style } from "./api";
import type { PhoneLang } from "./phoneText";

export interface LeagueWords {
  title: string;
  back: string;
  intro: string;
  yours: string;
  create: { heading: string; league: string; you: string; style: string; lang: string; button: string; busy: string };
  join: { heading: string; tab: string; code: string; codeHint: string; badCode: string; button: string; busy: string };
  places: string;
  prefs: (style: string, lang: string) => string;
  change: string;
  how: { heading: string; steps: [string, string][] };
  open: string;
  nav: { leagues: string; judges: string };
  style: Record<Style, string>;
  refused: {
    noCode: string;
    full: string;
    slowDown: string;
    problems: string;
    cannotStart: string;
    noLeagues: string;
    notYours: string;
    busy: string;
    other: (status: number) => string;
  };
  notMember: { heading: string; body: string; action: string };
  storageNote: string;
  readOnly: string;
  noPlayers: string;
  leaguesDown: string;
  alreadyIn: { text: (name: string) => string; open: string };
  loading: string;
  shareHeading: string;
  codeLabel: string;
  linkLabel: string;
  copy: string;
  copied: string;
  gameweek: (n: number) => string;
  members: string;
  bot: string;
  you: string;
  picked: string;
  notPicked: string;
  unknown: string;
  squad: {
    heading: string;
    yours: string;
    savedNote: string;
    change: string;
    save: string;
    saving: string;
    saved: string;
    cancel: string;
    captain: string;
    captainOf: (name: string) => string;
    count: (n: number, size: number) => string;
    club: (club: string, cap: number) => string;
    pickCaptain: string;
    rule: (size: number, cap: number) => string;
    progress: (n: number, size: number, cap: number) => string;
    tapCaptain: string;
    empty: string;
    player: string;
    position: string;
    clubHeading: string;
    add: string;
    remove: string;
    addOf: (name: string) => string;
    removeOf: (name: string) => string;
    clubFull: (club: string) => string;
    positions: Record<string, string>;
    filters: Record<"all" | "GK" | "DEF" | "MID" | "FWD", string>;
    showAll: (more: number) => string;
  };
  season: { heading: string; rank: string; name: string; points: string; played: string; empty: string };
  start: { button: (gameweek: number) => string; busy: string; unpicked: string };
}

export const LEAGUE_WORDS: Record<PhoneLang, LeagueWords> = {
  en: {
    title: "Play with your friends",
    back: "Back",
    intro: "Everyone picks five players and a captain. Then you watch the gameweek together, and the season table carries on.",
    yours: "Your leagues on this browser",
    create: { heading: "Start a league", league: "League name", you: "Your name", style: "How you like it",
              lang: "Language", button: "Create the league", busy: "Creating…" }, // prettier-ignore
    join: { heading: "Join a league", tab: "I have a code", code: "Join code", codeHint: "Six letters or numbers, from the friend who made it",
            badCode: "A join code is six letters or numbers.", button: "Join", busy: "Joining…" }, // prettier-ignore
    places: "Up to five friends; empty places are played by bots.",
    prefs: (style, lang) => `Your phone: ${style}, in ${lang}.`,
    change: "Change",
    how: {
      heading: "How a league works",
      steps: [
        ["Start it and share the code", "Up to five friends. Empty places are played by bots."],
        ["Everyone picks a squad", "Five players and a captain. Other friends' picks stay hidden."],
        ["Watch the gameweek together", "One shared screen; each friend's agent argues for their players."],
        ["The season table updates", "Points carry into the next gameweek."],
      ],
    },
    open: "Open",
    nav: { leagues: "Your leagues", judges: "For judges" },
    style: { casual: "Casual", analyst: "Analyst" },
    refused: {
      noCode: "No league has that code.",
      full: "Every seat in this league is taken.",
      slowDown: "Too many tries from here. Please wait a minute.",
      problems: "The server did not accept this:",
      cannotStart: "This server cannot start a league match yet.",
      noLeagues: "Leagues are not available on this server.",
      notYours: "The server did not accept this browser's place in the league. Open the join link again to take a seat.",
      busy: "Every seat is taken right now. Please try again in a minute.",
      other: (status) => `The server said ${status}. Please try again.`,
    },
    notMember: {
      heading: "This browser is not a member of this league",
      body: "Open the join link a friend in the league sent you, or enter their join code.",
      action: "Join with a code",
    },
    storageNote: "This browser could not keep your place in the league. It lasts while this tab is open.",
    readOnly: "You are looking at this league; it is not yours, so there is nothing to pick or start.",
    noPlayers: "The players for this gameweek could not be loaded. Please reload the page.",
    leaguesDown: "Leagues are not available on this server right now. Your place is kept; please try again later.",
    alreadyIn: { text: (name) => `This browser already has a seat in ${name}. Joining again takes a second seat.`,
                 open: "Open my seat" }, // prettier-ignore
    loading: "Loading the league…",
    shareHeading: "Invite your friends",
    codeLabel: "Join code",
    linkLabel: "Link to share",
    copy: "Copy invite link",
    copied: "Link copied",
    gameweek: (n) => `Gameweek ${n}`,
    members: "Members",
    bot: "Bot",
    you: "You",
    picked: "Picked",
    notPicked: "Not picked yet",
    unknown: "Not shown",
    squad: {
      heading: "Pick your squad",
      yours: "Your squad",
      savedNote: "You can change it until kickoff.",
      change: "Change squad",
      save: "Save my squad",
      saving: "Saving…",
      saved: "Squad saved",
      cancel: "Cancel",
      captain: "Captain",
      captainOf: (name) => `Make ${name} captain`,
      count: (n, size) => `Pick ${size - n > 0 ? `${size - n} more` : `${n - size} fewer`}: a squad has ${size} players.`,
      club: (club, cap) => `At most ${cap} players from ${club}.`,
      pickCaptain: "Choose a captain from your squad: their points count double.",
      rule: (size, cap) => `${size} players, at most ${cap} from one club, and a captain whose points count double.`,
      progress: (n, size, cap) => `${n} of ${size} · at most ${cap} from one club`,
      tapCaptain: "tap a player above to make them captain",
      empty: "Pick a player",
      player: "Player",
      position: "Position",
      clubHeading: "Club",
      add: "Add",
      remove: "Remove",
      addOf: (name) => `Add ${name}`,
      removeOf: (name) => `Remove ${name}`,
      clubFull: (club) => `${club} is full`,
      positions: { GK: "Goalkeeper", DEF: "Defender", MID: "Midfielder", FWD: "Forward" },
      filters: { all: "All", GK: "Goalkeepers", DEF: "Defenders", MID: "Midfielders", FWD: "Forwards" },
      showAll: (more) => `Show all (${more} more)`,
    },
    season: { heading: "Season table", rank: "#", name: "Name", points: "Points", played: "Played",
              empty: "No gameweek played yet." }, // prettier-ignore
    start: {
      button: (n) => `Start gameweek ${n}`,
      busy: "Starting…",
      unpicked: "You have not picked: start now and a squad is picked for you.",
    },
  },
  es: {
    title: "Juega con tus amigos",
    back: "Volver",
    intro: "Cada uno elige cinco jugadores y un capitán. Luego veis la jornada juntos, y la clasificación sigue.",
    yours: "Tus ligas en este navegador",
    create: { heading: "Crear una liga", league: "Nombre de la liga", you: "Tu nombre", style: "Cómo lo prefieres",
              lang: "Idioma", button: "Crear la liga", busy: "Creando…" }, // prettier-ignore
    join: { heading: "Unirse a una liga", tab: "Tengo un código", code: "Código", codeHint: "Seis letras o números, del amigo que la creó",
            badCode: "El código tiene seis letras o números.", button: "Unirme", busy: "Uniéndote…" }, // prettier-ignore
    places: "Hasta cinco amigos; las plazas libres las juegan bots.",
    prefs: (style, lang) => `Tu móvil: ${style}, en ${lang}.`,
    change: "Cambiar",
    how: {
      heading: "Cómo funciona una liga",
      steps: [
        ["Créala y comparte el código", "Hasta cinco amigos. Las plazas libres las juegan bots."],
        ["Cada uno elige su equipo", "Cinco jugadores y un capitán. Lo que eligen los demás no se ve."],
        ["Veis la jornada juntos", "Una pantalla compartida; el agente de cada amigo defiende a sus jugadores."],
        ["La clasificación se actualiza", "Los puntos pasan a la siguiente jornada."],
      ],
    },
    open: "Abrir",
    nav: { leagues: "Tus ligas", judges: "Para el jurado" },
    style: { casual: "Sencillo", analyst: "Analista" },
    refused: {
      noCode: "Ninguna liga tiene ese código.",
      full: "Todas las plazas de esta liga están ocupadas.",
      slowDown: "Demasiados intentos desde aquí. Espera un minuto.",
      problems: "El servidor no lo aceptó:",
      cannotStart: "Este servidor todavía no puede empezar un partido de liga.",
      noLeagues: "Las ligas no están disponibles en este servidor.",
      notYours: "El servidor no aceptó la plaza de este navegador en la liga. Abre otra vez el enlace para unirte.",
      busy: "Ahora mismo no quedan plazas. Inténtalo de nuevo en un minuto.",
      other: (status) => `El servidor respondió ${status}. Inténtalo de nuevo.`,
    },
    notMember: {
      heading: "Este navegador no es miembro de esta liga",
      body: "Abre el enlace que te envió un amigo de la liga, o escribe su código.",
      action: "Unirme con un código",
    },
    storageNote: "Este navegador no pudo guardar tu plaza en la liga. Dura mientras esta pestaña esté abierta.",
    readOnly: "Estás viendo esta liga; no es tuya, así que no hay nada que elegir ni empezar.",
    noPlayers: "No se pudieron cargar los jugadores de esta jornada. Vuelve a cargar la página.",
    leaguesDown: "Las ligas no están disponibles en este servidor ahora mismo. Tu plaza se guarda; inténtalo más tarde.",
    alreadyIn: { text: (name) => `Este navegador ya tiene una plaza en ${name}. Si te unes otra vez, ocupas otra plaza.`,
                 open: "Abrir mi plaza" }, // prettier-ignore
    loading: "Cargando la liga…",
    shareHeading: "Invita a tus amigos",
    codeLabel: "Código",
    linkLabel: "Enlace para compartir",
    copy: "Copiar el enlace",
    copied: "Enlace copiado",
    gameweek: (n) => `Jornada ${n}`,
    members: "Miembros",
    bot: "Bot",
    you: "Tú",
    picked: "Ha elegido",
    notPicked: "Sin elegir",
    unknown: "No se muestra",
    squad: {
      heading: "Elige tu equipo",
      yours: "Tu equipo",
      savedNote: "Puedes cambiarlo hasta el inicio.",
      change: "Cambiar equipo",
      save: "Guardar mi equipo",
      saving: "Guardando…",
      saved: "Equipo guardado",
      cancel: "Cancelar",
      captain: "Capitán",
      captainOf: (name) => `Hacer capitán a ${name}`,
      count: (n, size) => `${size - n > 0 ? `Elige ${size - n} más` : `Quita ${n - size}`}: un equipo tiene ${size} jugadores.`,
      club: (club, cap) => `Como mucho ${cap} jugadores de ${club}.`,
      pickCaptain: "Elige un capitán de tu equipo: sus puntos cuentan doble.",
      rule: (size, cap) => `${size} jugadores, como mucho ${cap} de un club, y un capitán cuyos puntos cuentan doble.`,
      progress: (n, size, cap) => `${n} de ${size} · como mucho ${cap} de un club`,
      tapCaptain: "toca un jugador de arriba para hacerlo capitán",
      empty: "Elige un jugador",
      player: "Jugador",
      position: "Posición",
      clubHeading: "Club",
      add: "Añadir",
      remove: "Quitar",
      addOf: (name) => `Añadir a ${name}`,
      removeOf: (name) => `Quitar a ${name}`,
      clubFull: (club) => `${club} está completo`,
      positions: { GK: "Portero", DEF: "Defensa", MID: "Centrocampista", FWD: "Delantero" },
      filters: { all: "Todos", GK: "Porteros", DEF: "Defensas", MID: "Centrocampistas", FWD: "Delanteros" },
      showAll: (more) => `Ver todos (${more} más)`,
    },
    season: { heading: "Clasificación", rank: "#", name: "Nombre", points: "Puntos", played: "Jugadas",
              empty: "Todavía no se ha jugado ninguna jornada." }, // prettier-ignore
    start: {
      button: (n) => `Empezar la jornada ${n}`,
      busy: "Empezando…",
      unpicked: "No has elegido: si empiezas ahora, se elige un equipo por ti.",
    },
  },
};
