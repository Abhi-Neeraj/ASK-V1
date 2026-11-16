// Lets the simulated HTTP client talk to a REAL server over a local TCP socket (plain HTTP, no TLS), so the
// firmware's own upload code can be checked against the real ASK API running on this computer.
//
// Used only when the simulator is started with --forward. Everything else uses the in-process fake server in
// hostsim_main.cpp.

#ifdef _WIN32
#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
typedef SOCKET sock_t;
#define CLOSESOCK closesocket
#else
#include <arpa/inet.h>
#include <netdb.h>
#include <sys/socket.h>
#include <unistd.h>
typedef int sock_t;
#define CLOSESOCK close
#define INVALID_SOCKET (-1)
#endif

#include <Arduino.h>

#include "hostsim_http.h"

namespace sim {

static bool send_all(sock_t s, const char* data, size_t length) {
  size_t sent = 0;
  while (sent < length) {
    const int n = (int)send(s, data + sent, (int)(length - sent), 0);
    if (n <= 0) return false;
    sent += (size_t)n;
  }
  return true;
}

HttpResponse forward_to_real(const HttpRequest& request) {
  HttpResponse out;
  out.transport_error = -1;

#ifdef _WIN32
  static bool started = false;
  if (!started) {
    WSADATA data;
    WSAStartup(MAKEWORD(2, 2), &data);
    started = true;
  }
#endif

  // http://host:port/path
  const std::string& url = request.url;
  const size_t scheme = url.find("://");
  if (scheme == std::string::npos) return out;
  const size_t slash = url.find('/', scheme + 3);
  const std::string authority = url.substr(scheme + 3, slash == std::string::npos ? std::string::npos : slash - scheme - 3);
  const size_t colon = authority.find(':');
  const std::string host = colon == std::string::npos ? authority : authority.substr(0, colon);
  const std::string port = colon == std::string::npos ? "80" : authority.substr(colon + 1);

  struct addrinfo hints;
  memset(&hints, 0, sizeof(hints));
  hints.ai_family = AF_INET;
  hints.ai_socktype = SOCK_STREAM;
  struct addrinfo* found = nullptr;
  if (getaddrinfo(host.c_str(), port.c_str(), &hints, &found) != 0 || found == nullptr) return out;
  sock_t s = socket(found->ai_family, found->ai_socktype, found->ai_protocol);
  if (s == INVALID_SOCKET || connect(s, found->ai_addr, (int)found->ai_addrlen) != 0) {
    if (s != INVALID_SOCKET) CLOSESOCK(s);
    freeaddrinfo(found);
    return out;
  }
  freeaddrinfo(found);

  std::string head = request.method + " " + request.path + " HTTP/1.1\r\nHost: " + authority + "\r\nConnection: close\r\n";
  for (const auto& kv : request.headers) {
    if (kv.first == "content-length") continue;
    head += kv.first + ": " + kv.second + "\r\n";
  }
  head += "Content-Length: " + std::to_string(request.body.size()) + "\r\n\r\n";
  if (!send_all(s, head.data(), head.size()) ||
      (!request.body.empty() && !send_all(s, (const char*)request.body.data(), request.body.size()))) {
    CLOSESOCK(s);
    return out;
  }

  std::string raw;
  char buffer[8192];
  for (;;) {
    const int n = (int)recv(s, buffer, sizeof(buffer), 0);
    if (n <= 0) break;
    raw.append(buffer, (size_t)n);
  }
  CLOSESOCK(s);

  const size_t end_of_head = raw.find("\r\n\r\n");
  if (raw.compare(0, 5, "HTTP/") != 0 || end_of_head == std::string::npos) return out;
  out.transport_error = 0;
  out.status = atoi(raw.c_str() + raw.find(' ') + 1);
  std::string body = raw.substr(end_of_head + 4);

  size_t at = raw.find("\r\n") + 2;
  while (at < end_of_head) {
    const size_t eol = raw.find("\r\n", at);
    const std::string line = raw.substr(at, eol - at);
    at = eol + 2;
    const size_t c = line.find(':');
    if (c == std::string::npos) continue;
    std::string key = line.substr(0, c);
    std::transform(key.begin(), key.end(), key.begin(), [](unsigned char ch) { return (char)tolower(ch); });
    size_t v = c + 1;
    while (v < line.size() && line[v] == ' ') v++;
    out.headers[key] = line.substr(v);
  }
  if (out.headers.count("transfer-encoding") && out.headers["transfer-encoding"].find("chunked") != std::string::npos) {
    out.chunked = true;  // the firmware cannot know the size of a chunked reply
  }
  out.body = body;
  return out;
}

}  // namespace sim
