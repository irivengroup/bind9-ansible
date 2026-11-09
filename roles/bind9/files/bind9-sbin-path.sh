#!/bin/bash
# Bind9-Ansible: append only when /usr/local/sbin is absent from PATH.
case ":${PATH:-}:" in
  *:/usr/local/sbin:*) ;;
  *) PATH="${PATH:+${PATH}:}/usr/local/sbin"; export PATH ;;
esac
