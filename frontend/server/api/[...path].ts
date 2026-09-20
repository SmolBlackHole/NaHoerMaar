import { playerProxy } from "../utils/playerProxy";

export default playerProxy(
	() => useRuntimeConfig().backendUrl,
	() => process.env.PUBLIC_ORIGIN || useRuntimeConfig().publicOrigin,
);
