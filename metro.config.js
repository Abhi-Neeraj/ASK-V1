const path = require("path");
const { getDefaultConfig } = require("expo/metro-config");

const config = getDefaultConfig(__dirname);

if (!config.resolver.assetExts.includes("onnx")) {
  config.resolver.assetExts.push("onnx");
}

// platform/ is a separate web + API project with its own dependency tree.
// Keep Metro from crawling it (its node_modules is large).
const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const platformDir = new RegExp(`^${escapeRegExp(path.join(__dirname, "platform"))}[\\\\/].*`);
const existingBlockList = config.resolver.blockList;
config.resolver.blockList = [
  ...(Array.isArray(existingBlockList) ? existingBlockList : existingBlockList ? [existingBlockList] : []),
  platformDir,
];

module.exports = config;
