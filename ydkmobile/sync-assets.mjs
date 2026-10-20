import { copyFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const root = dirname(fileURLToPath(import.meta.url));
for (const asset of ["app.js", "styles.css", "experience.js", "experience.css", "favicon.svg"]) {
  copyFileSync(resolve(root, "../app/static", asset), resolve(root, "www", asset));
}
console.log("Shared web assets copied to Android.");
