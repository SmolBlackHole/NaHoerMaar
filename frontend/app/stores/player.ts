import { defineStore } from "pinia";
import { createPlayerClient } from "~/player/client";

export const usePlayerStore = defineStore("player", () => createPlayerClient());
