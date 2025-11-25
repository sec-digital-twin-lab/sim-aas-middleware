#!/usr/bin/env python3

import argparse
import socket
import threading
import time
from typing import Optional


class CombinedTestServer:
    def __init__(self, host: str = '0.0.0.0', tcp_port: int = 8080, udp_port: int = 8081):
        self.host = host
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.tcp_socket: Optional[socket.socket] = None
        self.udp_socket: Optional[socket.socket] = None
        self.running = False
        
    def start(self):
        """Start both TCP and UDP test servers"""
        self.running = True
        
        # Start TCP server in a separate thread
        tcp_thread = threading.Thread(target=self._start_tcp_server)
        tcp_thread.daemon = True
        tcp_thread.start()
        
        # Start UDP server in a separate thread
        udp_thread = threading.Thread(target=self._start_udp_server)
        udp_thread.daemon = True
        udp_thread.start()
        
        print("Combined Test Server started:")
        print(f"  TCP on {self.host}:{self.tcp_port}")
        print(f"  UDP on {self.host}:{self.udp_port}")
        print("Press Ctrl+C to stop...")
        
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down servers...")
        finally:
            self.stop()
    
    def _start_tcp_server(self):
        """Start the TCP test server"""
        try:
            self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.tcp_socket.bind((self.host, self.tcp_port))
            self.tcp_socket.listen(5)
            
            print(f"TCP server listening on {self.host}:{self.tcp_port}")
            
            while self.running:
                try:
                    self.tcp_socket.settimeout(1.0)
                    try:
                        client_socket, address = self.tcp_socket.accept()
                        print(f"TCP connection from {address[0]}:{address[1]}")
                        
                        # Handle client in a separate thread
                        client_thread = threading.Thread(
                            target=self._handle_tcp_client, 
                            args=(client_socket, address)
                        )
                        client_thread.daemon = True
                        client_thread.start()
                        
                    except socket.timeout:
                        continue
                        
                except socket.error as e:
                    if self.running:
                        print(f"TCP socket error: {e}")
                    break
                    
        except Exception as e:
            print(f"TCP server error: {e}")
    
    def _handle_tcp_client(self, client_socket: socket.socket, address: tuple):
        """Handle individual TCP client connections"""
        try:
            time.sleep(0.1)  # Brief delay to simulate processing
            client_socket.close()
            print(f"TCP connection from {address[0]}:{address[1]} handled and closed")
        except Exception as e:
            print(f"Error handling TCP client {address}: {e}")
    
    def _start_udp_server(self):
        """Start the UDP test server"""
        try:
            self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.udp_socket.bind((self.host, self.udp_port))
            
            print(f"UDP server listening on {self.host}:{self.udp_port}")
            
            while self.running:
                try:
                    self.udp_socket.settimeout(1.0)
                    
                    try:
                        data, address = self.udp_socket.recvfrom(1024)
                        print(f"UDP packet from {address[0]}:{address[1]}: {data.decode('utf-8', errors='ignore')}")
                        
                        # Send a simple response back
                        response = b"connectivity_test_response"
                        self.udp_socket.sendto(response, address)
                        print(f"UDP response sent to {address[0]}:{address[1]}")
                        
                    except socket.timeout:
                        continue
                        
                except socket.error as e:
                    if self.running:
                        print(f"UDP socket error: {e}")
                    break
                    
        except Exception as e:
            print(f"UDP server error: {e}")
            
    def stop(self):
        """Stop both servers"""
        self.running = False
        
        if self.tcp_socket:
            try:
                self.tcp_socket.close()
            except Exception:
                pass
                
        if self.udp_socket:
            try:
                self.udp_socket.close()
            except Exception:
                pass
        
        print("All test servers stopped")


def main():
    parser = argparse.ArgumentParser(description='Combined TCP/UDP Test Server for connectivity testing')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to (default: 0.0.0.0)')
    parser.add_argument('--tcp-port', type=int, default=8080, help='TCP port to bind to (default: 8080)')
    parser.add_argument('--udp-port', type=int, default=8081, help='UDP port to bind to (default: 8081)')
    
    args = parser.parse_args()
    
    server = CombinedTestServer(args.host, args.tcp_port, args.udp_port)
    server.start()


if __name__ == '__main__':
    main()