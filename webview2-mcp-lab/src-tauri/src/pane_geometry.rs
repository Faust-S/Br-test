// Native coordinates come from the trusted shell's current CSS viewport,
// not from a fixed percentage or the guest document.
#[derive(Debug, PartialEq, Eq)]
pub struct PaneBounds { pub x:u32, pub y:u32, pub width:u32, pub height:u32 }

pub fn fit(client_width:u32, client_height:u32, viewport_width:f64, viewport_height:f64,
    left:f64, top:f64, right:f64, bottom:f64) -> Option<PaneBounds> {
    if client_width==0 || client_height==0 ||
        ![viewport_width,viewport_height,left,top,right,bottom].iter().all(|v|v.is_finite()) ||
        viewport_width<=0.0 || viewport_height<=0.0 ||
        left<0.0 || top<0.0 || right>viewport_width+0.5 || bottom>viewport_height+0.5 ||
        right<=left || bottom<=top { return None; }
    let x=(left/viewport_width*client_width as f64).round() as u32;
    let y=(top/viewport_height*client_height as f64).round() as u32;
    let r=(right.min(viewport_width)/viewport_width*client_width as f64).round() as u32;
    let b=(bottom.min(viewport_height)/viewport_height*client_height as f64).round() as u32;
    if r<=x || b<=y || r>client_width || b>client_height { return None; }
    Some(PaneBounds{x,y,width:r-x,height:b-y})
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn one_hundred_percent() {
        assert_eq!(fit(1000,700,1000.,700.,380.,56.,1000.,700.),Some(PaneBounds{x:380,y:56,width:620,height:644}));
    }
    #[test] fn dpi_150_percent() {
        assert_eq!(fit(1500,1050,1000.,700.,380.,56.,1000.,700.),Some(PaneBounds{x:570,y:84,width:930,height:966}));
    }
    #[test] fn viewport_zoom_and_resize() {
        assert_eq!(fit(960,600,800.,500.,400.,50.,800.,500.),Some(PaneBounds{x:480,y:60,width:480,height:540}));
    }
    #[test] fn rounding_preserves_client_edges() {
        let r=fit(1001,701,667.,467.,253.4,56.,667.1,467.1).unwrap();
        assert_eq!(r.x+r.width,1001);assert_eq!(r.y+r.height,701);
    }
    #[test] fn invalid_or_empty_rectangles_are_rejected() {
        for r in [fit(1000,700,1000.,700.,-1.,56.,1000.,700.),
            fit(1000,700,1000.,700.,380.,56.,1001.,700.),
            fit(1000,700,1000.,700.,380.,56.,380.,700.),
            fit(1000,700,f64::NAN,700.,380.,56.,1000.,700.),
            fit(0,700,1000.,700.,380.,56.,1000.,700.)] { assert!(r.is_none()); }
    }
}
