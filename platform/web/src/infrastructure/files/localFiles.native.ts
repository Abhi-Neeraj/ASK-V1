// Phone build: files under the app's documents folder (expo-file-system), kept until the app is removed.
import { Directory, File, Paths } from "expo-file-system";
import type { LocalFiles } from "@/infrastructure/files/localFiles";

export type { LocalFiles } from "@/infrastructure/files/localFiles";

function fileAt(path: string): File {
  return new File(Paths.document, ...path.split("/"));
}

export const localFiles: LocalFiles = {
  write: async (path, data) => {
    const parts = path.split("/");
    const dir = new Directory(Paths.document, ...parts.slice(0, -1));
    if (!dir.exists) dir.create({ intermediates: true });
    const file = fileAt(path);
    if (file.exists) file.delete();
    file.create();
    file.write(data);
  },
  read: async (path) => {
    const file = fileAt(path);
    return file.exists ? file.bytesSync() : null;
  },
  list: async (dir) => {
    const folder = new Directory(Paths.document, ...dir.split("/"));
    return folder.exists ? folder.list().map((entry) => entry.name) : [];
  },
  remove: async (path) => {
    const file = fileAt(path);
    if (file.exists) file.delete();
  },
};

export const KEEPS_FILES = true;
