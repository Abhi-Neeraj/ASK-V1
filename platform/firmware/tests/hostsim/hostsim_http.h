// See hostsim_http.cpp.

#pragma once

#include <Arduino.h>

namespace sim {

// Sends the request to the host and port in its URL over plain TCP and returns what came back. A refused or
// dropped connection comes back as transport_error = -1.
HttpResponse forward_to_real(const HttpRequest& request);

}  // namespace sim
