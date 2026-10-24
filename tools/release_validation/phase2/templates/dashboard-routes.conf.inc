    location = /progress {
      resolver 127.0.0.11 valid=30s ipv6=off;
      resolver_timeout 3s;
      set $progress_backend progress-dashboard:8080;
      proxy_pass http://$progress_backend$request_uri;
      proxy_set_header Host 47.110.76.9;
      proxy_set_header X-Forwarded-Proto https;
      client_max_body_size 3m;
    }
    location ^~ /progress/ {
      resolver 127.0.0.11 valid=30s ipv6=off;
      resolver_timeout 3s;
      set $progress_backend progress-dashboard:8080;
      proxy_pass http://$progress_backend$request_uri;
      proxy_set_header Host 47.110.76.9;
      proxy_set_header X-Forwarded-Proto https;
      client_max_body_size 3m;
    }
