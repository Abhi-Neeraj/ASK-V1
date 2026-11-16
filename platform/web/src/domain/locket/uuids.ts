// The locket's Bluetooth service and characteristics (spec 5.3). The firmware uses the same values.
export const LOCKET_UUIDS = {
  service: "69bc378a-a795-4f0c-984d-e2cee6c5ea54",
  info: "8242cd7a-098d-4b2d-9335-b9f4df3b7087",
  pairNonce: "30e12be5-e356-4896-aa4b-c78acd1cb186",
  control: "a4e0d5f7-39b8-4614-a440-5398719e89f1",
  bondProof: "7bac8620-f67c-4bd2-ac89-b6fcc04ebad7",
  status: "72666507-9e9a-40fb-a060-dda51aa64f35",
  audio: "3b73557c-0f90-4af2-9416-10b0c91aaf96",
} as const;

export const REQUESTED_MTU = 185;
