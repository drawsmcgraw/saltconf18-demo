touch_file:
  file.managed:
    - name: /etc/haproxy/haproxy.cfg
    - source: salt://haproxy/haproxy.cfg
    - template: jinja
    - mode: 644
