export const THEME_KEY = "ib:theme";

/**
 * Runs before the page paints so a saved choice never flashes the other theme first.
 * nginx stamps it with the CSP nonce like every other script.
 */
export const THEME_SCRIPT = `try{var t=localStorage.getItem("${THEME_KEY}");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;
