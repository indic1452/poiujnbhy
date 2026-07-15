import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";

// Без StrictMode: двойной маунт в dev ломает инициализацию Leaflet-карты.
ReactDOM.createRoot(document.getElementById("root")!).render(<App />);
