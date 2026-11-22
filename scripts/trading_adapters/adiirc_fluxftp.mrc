; AdiIRC add-on. Forward channel text to FluxFTP's authenticated loopback bridge.
; Load this file from Tools -> Edit Scripts. It never queues transfers itself.

on *:TEXT:*:#:{
  if ($len($1-) > 2048) return
  if ($left($1-,1) == $chr(1)) return
  var %socket = fttrade $+ $ticks $+ $rand(1000,999999)
  var %network = $encode($network,r)
  var %channel = $encode($chan,r)
  var %sender = $encode($nick,r)
  var %message = $encode($1-,r)
  var %event = $encode($network $+ : $+ $chan $+ : $+ $nick $+ : $+ $ctime $+ : $+ $ctimems,r)
  hadd -m fluxftp_trade %socket $+(%network,|,%channel,|,%sender,|,%message,|,%event)
  sockopen -4 %socket 127.0.0.1 __BRIDGE_PORT__
}

on *:sockopen:fttrade*:{
  if ($sockerr) {
    hdel fluxftp_trade $sockname
    return
  }
  var %data = $hget(fluxftp_trade,$sockname)
  sockwrite -n $sockname GET /v1/trading/irc-event?network=$gettok(%data,1,124) $+ &channel= $+ $gettok(%data,2,124) $+ &sender= $+ $gettok(%data,3,124) $+ &message= $+ $gettok(%data,4,124) $+ &event_id= $+ $gettok(%data,5,124) $+ &source_client=adiirc HTTP/1.1
  sockwrite -n $sockname Host: 127.0.0.1:__BRIDGE_PORT__
  sockwrite -n $sockname Authorization: Bearer __BRIDGE_TOKEN__
  sockwrite -n $sockname Connection: close
  sockwrite -n $sockname $crlf
  hdel fluxftp_trade $sockname
}

on *:sockclose:fttrade*:{
  hdel fluxftp_trade $sockname
}

on *:sockread:fttrade*:{
  sockread %fluxftp_trade_reply
}
