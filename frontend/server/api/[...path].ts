import { playerProxy } from "../utils/playerProxy";

export default playerProxy(() => useRuntimeConfig().backendUrl);
