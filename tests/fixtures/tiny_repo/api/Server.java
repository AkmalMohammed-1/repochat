package api;

import java.io.IOException;
import java.net.InetSocketAddress;
import com.sun.net.httpserver.HttpServer;
import com.sun.net.httpserver.HttpExchange;

/** Minimal HTTP server exposing the shop API. */
public class Server {
    private static final int PORT = 8080;

    /** Entry point: starts the server on PORT. */
    public static void main(String[] args) throws IOException {
        HttpServer server = HttpServer.create(new InetSocketAddress(PORT), 0);
        server.createContext("/health", Server::handleHealth);
        server.start();
        System.out.println("listening on " + PORT);
    }

    /** Responds 200 "ok" for load balancer health checks. */
    static void handleHealth(HttpExchange exchange) throws IOException {
        byte[] body = "ok".getBytes();
        exchange.sendResponseHeaders(200, body.length);
        exchange.getResponseBody().write(body);
        exchange.close();
    }
}
