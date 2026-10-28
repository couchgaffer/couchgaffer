import { Judges } from "./pages/Judges";
import { LeagueHome, LeaguePage } from "./pages/League";
import { Landing, Lobby } from "./pages/Lobby";
import { PhoneOnly } from "./pages/PhoneOnly";
import { Watch } from "./pages/Watch";
import { useRoute } from "./router";

export function App() {
  const route = useRoute();
  switch (route.page) {
    case "landing":
      return <Landing />;
    case "watch-now":
      return <Lobby />;
    case "judges":
      return <Judges />;
    case "watch":
      return <Watch key={route.session} session={route.session} />;
    case "phone":
      return <PhoneOnly key={route.session} session={route.session} manager={route.manager} />;
    case "league-home":
      // A new query string is a new form: /league/join?code=X to /league shows the start form, without the code (#196)
      return <LeagueHome key={location.search} search={location.search} />;
    case "league":
      return <LeaguePage key={route.league} league={route.league} />;
    case "missing":
      return (
        <main className="notice">
          <h1>Nothing here</h1>
          <p><a href="/">Back to Couch Gaffer</a></p>
        </main>
      );
  }
}
