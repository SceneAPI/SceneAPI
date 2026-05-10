// Smoke test: pluggable-transport HTTP client with an in-memory mock.
// No real network access, no external HTTP library — proves the
// pluggable-transport design works.

#include <cassert>
#include <cstdint>
#include <iostream>
#include <string>

#include "sfmapi/client.hpp"

namespace {

// A tiny in-memory transport: records the last request, returns a
// canned response so we can assert that the URL + method + body the
// client builds are correct.
struct MockTransport {
  mutable sfmapi::HttpRequest last_req;
  sfmapi::HttpResponse next_response{200, {}, {}};

  sfmapi::HttpResponse operator()(const sfmapi::HttpRequest& req) const {
    last_req = req;
    return next_response;
  }
};

void TestBuildsCapabilitiesUrl() {
  auto mock = std::make_shared<MockTransport>();
  sfmapi::Client c({"http://x:8080/", "key", [mock](const sfmapi::HttpRequest& r) {
                      return (*mock)(r);
                    }});
  auto resp = c.Capabilities();
  assert(resp.status == 200);
  assert(mock->last_req.method == "GET");
  assert(mock->last_req.url == "http://x:8080/v1/capabilities");
  assert(mock->last_req.headers.at("Authorization") == "Bearer key");
  std::cout << "  capabilities URL OK\n";
}

void TestTrailingSlashStripped() {
  auto mock = std::make_shared<MockTransport>();
  sfmapi::Client c({"http://x///", "", [mock](const sfmapi::HttpRequest& r) {
                      return (*mock)(r);
                    }});
  c.Healthz();
  assert(mock->last_req.url == "http://x/healthz");
  std::cout << "  trailing-slash stripping OK\n";
}

void TestPostBodyAndContentType() {
  auto mock = std::make_shared<MockTransport>();
  sfmapi::Client c({"http://x", "", [mock](const sfmapi::HttpRequest& r) {
                      return (*mock)(r);
                    }});
  c.SubmitDense("01HGABC");
  assert(mock->last_req.method == "POST");
  assert(mock->last_req.url == "http://x/v1/reconstructions/01HGABC/dense");
  assert(mock->last_req.headers.at("Content-Type") == "application/json");
  std::cout << "  POST shape OK\n";
}

void TestRaiseOnErrorExtractsCapability() {
  sfmapi::HttpResponse resp;
  resp.status = 501;
  std::string body =
      R"({"type":"...","title":"x","status":501,"detail":"y","capability":"dense.patch_match_stereo"})";
  resp.body.assign(body.begin(), body.end());
  bool threw = false;
  try {
    sfmapi::Client::RaiseOnError(resp);
  } catch (const sfmapi::HttpStatusError& e) {
    threw = true;
    assert(e.status() == 501);
    assert(e.capability() == "dense.patch_match_stereo");
  }
  assert(threw && "expected HttpStatusError on 501");
  std::cout << "  RaiseOnError extracts capability OK\n";
}

void TestApiKeyOnlySetWhenPresent() {
  auto mock = std::make_shared<MockTransport>();
  sfmapi::Client c({"http://x", "", [mock](const sfmapi::HttpRequest& r) {
                      return (*mock)(r);
                    }});
  c.GetProject("01HG");
  assert(mock->last_req.headers.find("Authorization") ==
         mock->last_req.headers.end());
  std::cout << "  no Authorization header without api_key OK\n";
}

void TestPatchChunkContentRange() {
  auto mock = std::make_shared<MockTransport>();
  sfmapi::Client c({"http://x", "", [mock](const sfmapi::HttpRequest& r) {
                      return (*mock)(r);
                    }});
  std::vector<std::uint8_t> data{'A', 'B', 'C'};
  c.PatchChunk("u1", data, 0);
  assert(mock->last_req.method == "PATCH");
  assert(mock->last_req.headers.at("Content-Range") == "bytes 0-2/3");
  std::cout << "  PATCH Content-Range OK\n";
}

void TestExtendedEndpoints() {
  auto mock = std::make_shared<MockTransport>();
  sfmapi::Client c({"http://x", "", [mock](const sfmapi::HttpRequest& r) {
                      return (*mock)(r);
                    }});

  c.Readyz();
  assert(mock->last_req.url == "http://x/readyz");

  c.PatchProject("p1", "{\"name\":\"x\"}");
  assert(mock->last_req.method == "PATCH");
  assert(mock->last_req.url == "http://x/v1/projects/p1");

  c.PatchDataset("p1", "d1", "{}");
  assert(mock->last_req.url == "http://x/v1/projects/p1/datasets/d1");

  c.BatchCreateImages("d1", "{\"requests\":[]}");
  assert(mock->last_req.url == "http://x/v1/datasets/d1/images:batchCreate");

  c.DeleteImage("d1", "x.jpg");
  assert(mock->last_req.method == "DELETE");
  assert(mock->last_req.url == "http://x/v1/datasets/d1/images/x.jpg");

  c.GetImageBytes("img1", true);
  assert(mock->last_req.url == "http://x/v1/images/img1/bytes?download=true");

  c.GetImageThumbnail("img1", 256);
  assert(mock->last_req.url == "http://x/v1/images/img1/thumbnail?size=256");

  c.GetUpload("u1");
  assert(mock->last_req.url == "http://x/v1/uploads/u1");

  c.GetSubmodel("s1");
  assert(mock->last_req.url == "http://x/v1/submodels/s1");

  c.ListArtifactKinds("", 25);
  assert(mock->last_req.url == "http://x/v1/artifacts/kinds?page_size=25");

  c.GetArtifact("a1");
  assert(mock->last_req.url == "http://x/v1/artifacts/a1");

  c.ReadArtifactContent("a1", true);
  assert(mock->last_req.url == "http://x/v1/artifacts/a1/content?download=true");

  c.ListJobArtifacts("j1", "features.database", "", "", "tok", 50);
  assert(mock->last_req.url ==
         "http://x/v1/jobs/j1/artifacts?kind=features.database&page_token=tok&page_size=50");

  c.ListReconstructionArtifacts("r1", "matches.raw", "t1", "matches");
  assert(mock->last_req.url ==
         "http://x/v1/reconstructions/r1/artifacts?kind=matches.raw&task_id=t1&name=matches");

  c.ReadImageObservations("r1", 5, "img1");
  assert(mock->last_req.url ==
         "http://x/v1/reconstructions/r1/snapshots/5/images/img1/observations");

  c.ReadPointVisibility("r1", 5, "42");
  assert(mock->last_req.url ==
         "http://x/v1/reconstructions/r1/snapshots/5/points/42/visibility");

  c.ReadTilesIndex("r1", 5, 4);
  assert(mock->last_req.url ==
         "http://x/v1/reconstructions/r1/snapshots/5/tiles/index.json?max_level=4");

  c.ReadTile("r1", 5, 2, 1, 0, 3);
  assert(mock->last_req.url ==
         "http://x/v1/reconstructions/r1/snapshots/5/tiles/2/1/0/3.bin");

  c.ListApiKeys();
  assert(mock->last_req.url == "http://x/v1/admin/api-keys");

  c.DeleteApiKey("k1");
  assert(mock->last_req.method == "DELETE");
  assert(mock->last_req.url == "http://x/v1/admin/api-keys/k1");

  std::cout << "  extended endpoints OK\n";
}

void TestUploadBytesConvenienceMethod() {
  // Three calls expected: InitUpload, PatchChunk, FinalizeUpload.
  // Use a transport that hands back canned responses based on URL.
  struct Recorder {
    std::vector<sfmapi::HttpRequest> calls;
    sfmapi::HttpResponse operator()(const sfmapi::HttpRequest& r) {
      calls.push_back(r);
      sfmapi::HttpResponse resp;
      resp.status = 200;
      if (r.url.find(":finalize") != std::string::npos) {
        std::string b = R"({"upload_id":"u1","state":"finalized","blob_sha":"abc"})";
        resp.body.assign(b.begin(), b.end());
      } else if (r.method == "POST" && r.url.find("/v1/uploads") != std::string::npos) {
        std::string b = R"({"upload_id":"u1","state":"open","expected_size":3,"received_bytes":0,"blob_sha":null,"expires_at":"x"})";
        resp.body.assign(b.begin(), b.end());
      } else {
        std::string b = R"({"upload_id":"u1","state":"received"})";
        resp.body.assign(b.begin(), b.end());
      }
      return resp;
    }
  };
  auto rec = std::make_shared<Recorder>();
  sfmapi::Client c(
      {"http://x", "", [rec](const sfmapi::HttpRequest& r) { return (*rec)(r); }});
  std::vector<std::uint8_t> data{'A', 'B', 'C'};
  auto resp = c.UploadBytes(data);
  assert(resp.status == 200);
  // 3 calls: init + 1 chunk + finalize
  assert(rec->calls.size() == 3);
  assert(rec->calls[0].method == "POST");
  assert(rec->calls[1].method == "PATCH");
  assert(rec->calls[2].url.find(":finalize") != std::string::npos);
  std::cout << "  UploadBytes convenience OK\n";
}

void TestWaitForJobAndParseJobDetail() {
  // 1. ParseJobDetail decodes a real fixture-shaped body.
  {
    sfmapi::HttpResponse resp;
    resp.status = 200;
    std::string body = R"({
      "job_id":"01HZJOB000000000000000000",
      "tenant_id":"default",
      "project_id":"01HZPROJ00000000000000000",
      "recipe":"features",
      "status":"failed",
      "cancel_requested":false,
      "cancel_force":false,
      "created_at":"2026-05-05T00:00:00Z",
      "started_at":"2026-05-05T00:00:01Z",
      "finished_at":"2026-05-05T00:00:02Z",
      "error_class":"FileNotFoundError",
      "error_message":"missing image",
      "tasks":[
        {"task_id":"01HZTASK0000000000000000A","job_id":"01HZJOB000000000000000000",
         "kind":"extract","status":"failed","cache_key":"abc","inputs_hash":"i","params_hash":"p",
         "outputs_ref":null}
      ]
    })";
    resp.body.assign(body.begin(), body.end());
    auto detail = sfmapi::Client::ParseJobDetail(resp);
    assert(detail.job_id == "01HZJOB000000000000000000");
    assert(detail.status == "failed");
    assert(detail.recipe == "features");
    assert(detail.error_class == "FileNotFoundError");
    assert(detail.error_message == "missing image");
    assert(detail.tasks.size() == 1);
    assert(detail.tasks[0].kind == "extract");
    assert(detail.tasks[0].status == "failed");
  }

  // 2. IsTerminalJobStatus covers all four terminal states.
  for (const auto& s : {"succeeded", "failed", "cancelled", "cancelled_dirty"}) {
    assert(sfmapi::Client::IsTerminalJobStatus(s));
  }
  for (const auto& s : {"pending", "running", "weird"}) {
    assert(!sfmapi::Client::IsTerminalJobStatus(s));
  }

  // 3. WaitForJob polls until terminal then returns.
  struct PollRecorder {
    std::vector<sfmapi::HttpRequest> calls;
    std::vector<std::string> states;
    int idx = 0;
    sfmapi::HttpResponse operator()(const sfmapi::HttpRequest& r) {
      calls.push_back(r);
      sfmapi::HttpResponse resp;
      resp.status = 200;
      const std::string status =
          states[(idx < static_cast<int>(states.size())) ? idx++ : static_cast<int>(states.size()) - 1];
      std::string body = std::string("{\"job_id\":\"01HZJOB000000000000000000\",") +
                         "\"tenant_id\":\"t\",\"project_id\":\"p\",\"recipe\":\"r\"," +
                         "\"status\":\"" + status + "\"," +
                         "\"cancel_requested\":false,\"cancel_force\":false," +
                         "\"created_at\":\"2026-05-05T00:00:00Z\",\"tasks\":[]}";
      resp.body.assign(body.begin(), body.end());
      return resp;
    }
  };
  auto rec = std::make_shared<PollRecorder>();
  rec->states = {"pending", "running", "succeeded"};
  sfmapi::Client c({"http://x", "", [rec](const sfmapi::HttpRequest& r) {
                      return (*rec)(r);
                    }});
  int sleeps = 0;
  auto detail = c.WaitForJob(
      "01HZJOB000000000000000000", /*poll_interval_ms=*/0, /*timeout_ms=*/5000,
      [&sleeps](int) { ++sleeps; });
  assert(detail.status == "succeeded");
  assert(rec->calls.size() == 3);
  assert(sleeps == 2);  // slept between polls 1->2 and 2->3, not after terminal

  // 4. WaitForJob throws on timeout when status never goes terminal.
  auto rec2 = std::make_shared<PollRecorder>();
  rec2->states = {"pending"};  // never advances
  sfmapi::Client c2({"http://x", "", [rec2](const sfmapi::HttpRequest& r) {
                       return (*rec2)(r);
                     }});
  bool threw = false;
  try {
    c2.WaitForJob("01HZJOB000000000000000000", /*poll_interval_ms=*/0,
                  /*timeout_ms=*/0, [](int) {});
  } catch (const std::runtime_error& e) {
    threw = true;
    std::string msg = e.what();
    assert(msg.find("still in status") != std::string::npos);
  }
  assert(threw);

  // 5. WaitForJob raises typed SfmApiError on a 404 from the polling GET.
  auto rec404 = std::make_shared<PollRecorder>();
  // Override operator() by using a different transport.
  auto t404 = [](const sfmapi::HttpRequest&) {
    sfmapi::HttpResponse r;
    r.status = 404;
    std::string body =
        R"({"status":404,"title":"Resource not found","detail":"Job missing"})";
    r.body.assign(body.begin(), body.end());
    return r;
  };
  sfmapi::Client c3({"http://x", "", t404});
  bool threw404 = false;
  try {
    c3.WaitForJob("01HZJOB000000000000000000", 0, 5000, [](int) {});
  } catch (const sfmapi::HttpStatusError& e) {
    threw404 = (e.status() == 404);
  }
  assert(threw404);

  std::cout << "  WaitForJob + ParseJobDetail OK\n";
}

void TestSubmitAndWait() {
  // Transport that:
  //   - returns a 202 JobAcceptedResponse on the first POST it sees,
  //   - then flips status pending -> succeeded over 2 GET polls.
  struct ChainTransport {
    std::vector<sfmapi::HttpRequest> calls;
    int poll = 0;
    sfmapi::HttpResponse operator()(const sfmapi::HttpRequest& r) {
      calls.push_back(r);
      sfmapi::HttpResponse resp;
      if (r.method == "POST") {
        resp.status = 202;
        std::string body =
            R"({"job_id":"01HZJOB000000000000000000","task_ids":["01HZTASK0000000000000000A"]})";
        resp.body.assign(body.begin(), body.end());
        return resp;
      }
      // Poll: GET /v1/jobs/{id}
      resp.status = 200;
      const std::vector<std::string> states = {"pending", "succeeded"};
      const std::string status =
          states[(poll < static_cast<int>(states.size())) ? poll++ : static_cast<int>(states.size()) - 1];
      std::string body = std::string(R"({"job_id":"01HZJOB000000000000000000",)") +
                         R"("tenant_id":"t","project_id":"p","recipe":"r","status":")" +
                         status + R"(",)" +
                         R"("cancel_requested":false,"cancel_force":false,)" +
                         R"("created_at":"2026-05-05T00:00:00Z","tasks":[]})";
      resp.body.assign(body.begin(), body.end());
      return resp;
    }
  };
  auto chain = std::make_shared<ChainTransport>();
  sfmapi::Client c({"http://x", "", [chain](const sfmapi::HttpRequest& r) {
                      return (*chain)(r);
                    }});

  auto detail = c.SubmitAndWait(
      [&]() { return c.SubmitFeatures("ds_test", "{}"); },
      /*poll_interval_ms=*/0,
      /*timeout_ms=*/5000,
      /*sleep_fn=*/[](int) {});
  assert(detail.job_id == "01HZJOB000000000000000000");
  assert(detail.status == "succeeded");
  // Expect: 1 POST submit + 2 GET polls = 3 calls total.
  assert(chain->calls.size() == 3);
  assert(chain->calls[0].method == "POST");
  assert(chain->calls[1].method == "GET");
  assert(chain->calls[2].method == "GET");

  // Empty job_id from submit -> std::runtime_error.
  auto empty = [](const sfmapi::HttpRequest&) {
    sfmapi::HttpResponse r;
    r.status = 202;
    std::string body = R"({"task_ids":[]})";
    r.body.assign(body.begin(), body.end());
    return r;
  };
  sfmapi::Client c2({"http://x", "", empty});
  bool threw = false;
  try {
    c2.SubmitAndWait(
        [&]() { return c2.SubmitFeatures("ds", "{}"); }, 0, 5000, [](int) {});
  } catch (const std::runtime_error& e) {
    threw = std::string(e.what()).find("no job_id") != std::string::npos;
  }
  assert(threw);

  std::cout << "  SubmitAndWait OK\n";
}

void TestSubmitAndStream() {
  // Transport returns:
  //   - 202 on POST submit,
  //   - 200 SSE body on GET /v1/jobs/{id}/events,
  //   - 200 succeeded on next GET /v1/jobs/{id}.
  struct StreamTransport {
    std::vector<sfmapi::HttpRequest> calls;
    sfmapi::HttpResponse operator()(const sfmapi::HttpRequest& r) {
      calls.push_back(r);
      sfmapi::HttpResponse resp;
      if (r.method == "POST") {
        resp.status = 202;
        std::string body = R"({"job_id":"01HZJOB000000000000000000","task_ids":[]})";
        resp.body.assign(body.begin(), body.end());
        return resp;
      }
      if (r.url.find("/events") != std::string::npos) {
        resp.status = 200;
        resp.headers["content-type"] = "text/event-stream";
        std::string body =
            "id: 1\nevent: progress\ndata: {\"phase\":\"extract\"}\n\n"
            "id: 2\nevent: progress\ndata: {\"phase\":\"match\"}\n\n";
        resp.body.assign(body.begin(), body.end());
        return resp;
      }
      // GET /v1/jobs/{id}
      resp.status = 200;
      std::string body =
          R"({"job_id":"01HZJOB000000000000000000","tenant_id":"t","project_id":"p",)"
          R"("recipe":"r","status":"succeeded","cancel_requested":false,)"
          R"("cancel_force":false,"created_at":"2026-05-05T00:00:00Z","tasks":[]})";
      resp.body.assign(body.begin(), body.end());
      return resp;
    }
  };
  auto rec = std::make_shared<StreamTransport>();
  sfmapi::Client c({"http://x", "", [rec](const sfmapi::HttpRequest& r) {
                      return (*rec)(r);
                    }});
  std::vector<std::string> seen_ids;
  auto detail = c.SubmitAndStream(
      [&]() { return c.SubmitFeatures("ds_test", "{}"); },
      [&](const sfmapi::SseEvent& ev) { seen_ids.push_back(ev.id); },
      /*poll_interval_ms=*/0,
      /*timeout_ms=*/5000,
      /*sleep_fn=*/[](int) {});
  assert(detail.status == "succeeded");
  assert(seen_ids.size() == 2);
  assert(seen_ids[0] == "1");
  assert(seen_ids[1] == "2");
  // Calls: 1 POST + 1 GET /events + 1 GET /v1/jobs/{id}
  assert(rec->calls.size() == 3);
  std::cout << "  SubmitAndStream OK\n";
}

void TestStreamEventsInstanceMethod() {
  // Client::StreamEvents fetches GET /v1/jobs/{id}/events and
  // decodes the buffered body in one call. Throws on non-2xx.
  auto ok = [](const sfmapi::HttpRequest& r) {
    sfmapi::HttpResponse resp;
    if (r.url.find("/events") == std::string::npos) {
      resp.status = 500;
      return resp;
    }
    resp.status = 200;
    resp.headers["content-type"] = "text/event-stream";
    std::string body =
        "id: 10\nevent: progress\ndata: hello\n\n"
        "id: 11\nevent: progress\ndata: world\n\n";
    resp.body.assign(body.begin(), body.end());
    return resp;
  };
  sfmapi::Client c({"http://x", "k1", ok});
  auto evs = c.StreamEvents("01HZJOB000000000000000000");
  assert(evs.size() == 2);
  assert(evs[0].id == "10");
  assert(evs[0].data == "hello");
  assert(evs[1].id == "11");
  assert(evs[1].data == "world");

  // Bad status -> typed HttpStatusError.
  auto bad = [](const sfmapi::HttpRequest&) {
    sfmapi::HttpResponse r;
    r.status = 404;
    return r;
  };
  sfmapi::Client c2({"http://x", "", bad});
  bool threw = false;
  try {
    c2.StreamEvents("01HZJOB000000000000000000");
  } catch (const sfmapi::HttpStatusError& e) {
    threw = (e.status() == 404);
  }
  assert(threw);
  std::cout << "  StreamEvents instance OK\n";
}

void TestResourcePodStructsCompile() {
  // Verify the new POD structs exist with sane defaults and the
  // expected fields.
  sfmapi::Project p;
  p.project_id = "01HG";
  p.name = "x";
  sfmapi::Dataset d;
  d.is_spherical = true;
  sfmapi::ImageRow img;
  img.byte_size = 1024;
  sfmapi::Upload u;
  u.expected_size = 1000;
  sfmapi::ArtifactKind ak;
  ak.kind = "features.database";
  sfmapi::StageArtifact artifact;
  artifact.artifact_id = "01HZARTIFACT000000000000";
  artifact.kind = ak.kind;
  sfmapi::Job j;
  j.cancel_force = true;
  sfmapi::JobDetail jd;
  jd.tasks.push_back({});
  sfmapi::Page<sfmapi::Project> page;
  page.items.push_back(p);
  sfmapi::ApiKeyCreated key;
  key.raw_key = "secret";
  sfmapi::HealthResponse hr;
  hr.status = "ok";
  sfmapi::VersionResponse vr;
  vr.sfmapi = "0.0.1";
  // backend is std::optional<BackendVersion>; default-constructed
  // VersionResponse leaves it unset.
  assert(p.project_id == "01HG");
  assert(d.is_spherical);
  assert(img.byte_size == 1024);
  assert(artifact.kind == "features.database");
  assert(jd.tasks.size() == 1);
  assert(page.items.size() == 1);
  assert(key.raw_key == "secret");
  std::cout << "  resource POD structs OK\n";
}

}  // namespace

int main() {
  std::cout << "sfmapi C++ client smoke tests:\n";
  TestBuildsCapabilitiesUrl();
  TestTrailingSlashStripped();
  TestPostBodyAndContentType();
  TestRaiseOnErrorExtractsCapability();
  TestApiKeyOnlySetWhenPresent();
  TestPatchChunkContentRange();
  TestExtendedEndpoints();
  TestUploadBytesConvenienceMethod();
  TestWaitForJobAndParseJobDetail();
  TestSubmitAndWait();
  TestSubmitAndStream();
  TestStreamEventsInstanceMethod();
  TestResourcePodStructsCompile();
  std::cout << "all OK\n";
  return 0;
}
