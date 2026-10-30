package com.minty.assistant;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.mock.web.MockMultipartFile;
import java.nio.file.*;
import java.time.*;
import java.util.*;
import static org.assertj.core.api.Assertions.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@SpringBootTest(properties = {
        "spring.datasource.url=jdbc:h2:mem:minty-test;MODE=PostgreSQL;DB_CLOSE_DELAY=-1",
        "spring.datasource.driver-class-name=org.h2.Driver", "spring.datasource.username=sa", "spring.datasource.password=",
        "spring.jpa.hibernate.ddl-auto=create-drop", "agent.api-key=test-key", "agent.timeout-seconds=10"
})
@AutoConfigureMockMvc
class AssistantIntegrationTest {
    static final Path root;
    static {
        try {
            root = Files.createTempDirectory("minty-backend-test-");
            Files.writeString(root.resolve("worker.py"), """
                    import json, sys, time
                    from pathlib import Path
                    data = json.load(sys.stdin)
                    if data['prompt'] == 'slow':
                        time.sleep(30)
                    if data['prompt'] == 'error':
                        print(json.dumps({'type':'error','content':'controlled failure'}), flush=True)
                        sys.exit(1)
                    workspace = Path(sys.argv[sys.argv.index('--workspace') + 1])
                    (workspace / 'hello.txt').write_text('hello from agent', encoding='utf-8')
                    (workspace / '.tasks').mkdir(exist_ok=True)
                    (workspace / '.tasks' / 'task_1.json').write_text(json.dumps({'id':1,'subject':'Write greeting','status':'completed'}))
                    for event in [
                      {'type':'tool_start','content':json.dumps({'name':'write_file','input':{'path':'hello.txt'}})},
                      {'type':'tool_result','content':json.dumps({'name':'write_file','output':'OK'})},
                      {'type':'text','content':'Finished!'},
                      {'type':'usage','inputTokens':12,'outputTokens':7},
                      {'type':'done','content':''}]:
                        print(json.dumps(event), flush=True)
                    """);
        } catch (Exception e) { throw new ExceptionInInitializerError(e); }
    }
    @DynamicPropertySource static void properties(DynamicPropertyRegistry registry) {
        registry.add("agent.worker", () -> root.resolve("worker.py").toString());
        registry.add("agent.workspaces", () -> root.resolve("workspaces").toString());
        registry.add("agent.python", () -> System.getenv().getOrDefault("AGENT_TEST_PYTHON", "python"));
    }
    @Autowired AgentService agent;
    @Autowired WorkspaceService workspaces;
    @Autowired MockMvc mvc;
    @Autowired ObjectMapper json;

    @Test void completeRunPersistsMessagesEventsFilesTasksAndUsage() throws Exception {
        ChatSession session = agent.createSession(null);
        AgentRun run = agent.start(session.id, "write a greeting");
        AgentRun finished = awaitRun(run.id);
        assertThat(finished.status).isEqualTo("completed");
        assertThat(finished.inputTokens).isEqualTo(12);
        assertThat(finished.outputTokens).isEqualTo(7);
        assertThat(agent.requireSession(session.id).title).isEqualTo("write a greeting");
        List<?> messages = (List<?>) agent.session(session.id).get("messages");
        assertThat(messages).hasSize(2);
        assertThat(agent.events(run.id, 0)).extracting(e -> e.type).contains("tool_start", "tool_result", "text", "status");
        assertThat(workspaces.content(session.id, "hello.txt").get("content")).isEqualTo("hello from agent");
        assertThat(workspaces.tasks(session.id)).hasSize(1);
        assertThat(workspaces.files(session.id)).hasSize(1);
        assertThat((Long) agent.stats().get("totalTokens")).isGreaterThanOrEqualTo(19);
    }
    @Test void concurrentSessionRunIsRejectedAndStopIsTerminal() throws Exception {
        ChatSession session = agent.createSession(null);
        AgentRun run = agent.start(session.id, "slow");
        try {
            assertThatThrownBy(() -> agent.start(session.id, "second task"))
                    .isInstanceOf(ApiException.class).hasMessageContaining("正在运行");
        } finally { agent.stop(run.id); }
        assertThat(awaitRun(run.id).status).isEqualTo("stopped");
        Thread.sleep(150);
        assertThat(agent.requireRun(run.id).status).isEqualTo("stopped");
    }
    @Test void workerErrorBecomesFailedRun() throws Exception {
        ChatSession session = agent.createSession(null);
        AgentRun run = agent.start(session.id, "error");
        AgentRun finished = awaitRun(run.id);
        assertThat(finished.status).isEqualTo("failed");
        assertThat(finished.error).contains("controlled failure");
    }
    @Test void filePreviewCannotEscapeSessionOrReadHiddenFiles() throws Exception {
        ChatSession session = agent.createSession(null);
        Path workspace = workspaces.workspace(session.id);
        Files.writeString(workspace.resolve(".env"), "SECRET=keep-private");
        assertThatThrownBy(() -> workspaces.content(session.id, "../outside.txt")).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> workspaces.content(session.id, ".env")).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> workspaces.content(session.id, root.toAbsolutePath().toString())).isInstanceOf(ApiException.class);
        assertThat(workspaces.files(session.id)).isEmpty();
    }
    @Test void apiRejectsBlankPromptsAndUntrustedBrowserOrigins() throws Exception {
        ChatSession session = agent.createSession(null);
        mvc.perform(post("/api/sessions/" + session.id + "/runs").contentType(MediaType.APPLICATION_JSON).content("{\"prompt\":\"  \"}"))
                .andExpect(status().isBadRequest());
        mvc.perform(post("/api/sessions").header("Origin", "https://untrusted.example").contentType(MediaType.APPLICATION_JSON).content("{}"))
                .andExpect(status().isForbidden());
        mvc.perform(get("/api/status")).andExpect(status().isOk()).andExpect(jsonPath("$.configured").value(true))
                .andExpect(jsonPath("$.apiKey").doesNotExist());
    }
    @Test void uploadAcceptsUtf8CodeAndRejectsEscapeBinaryAndActiveRunWrites() throws Exception {
        ChatSession session = agent.createSession(null);
        mvc.perform(multipart("/api/sessions/" + session.id + "/files")
                        .file(new MockMultipartFile("file", "hello.py", "text/plain", "print('你好')".getBytes(java.nio.charset.StandardCharsets.UTF_8)))
                        .param("path", "src/hello.py"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.path").value("src/hello.py"));
        assertThat(workspaces.content(session.id, "src/hello.py").get("content")).contains("你好");
        assertThatThrownBy(() -> agent.upload(session.id, "../outside.py", new byte[]{65})).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> agent.upload(session.id, ".env", new byte[]{65})).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> agent.upload(session.id, "binary.png", new byte[]{0, 1})).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> agent.upload(session.id, "invalid.txt", new byte[]{(byte) 0xff})).isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> agent.upload(session.id, "large.txt", new byte[1048577])).isInstanceOf(ApiException.class);
        AgentRun run = agent.start(session.id, "slow");
        try { assertThatThrownBy(() -> agent.upload(session.id, "late.txt", new byte[]{65})).isInstanceOf(ApiException.class).hasMessageContaining("停止"); }
        finally { agent.stop(run.id); }
    }
    private AgentRun awaitRun(String id) throws Exception {
        Instant deadline = Instant.now().plusSeconds(8);
        AgentRun run;
        do { run = agent.requireRun(id); if (!run.status.equals("running")) return run; Thread.sleep(40); }
        while (Instant.now().isBefore(deadline));
        throw new AssertionError("Run did not finish: " + id);
    }
}
